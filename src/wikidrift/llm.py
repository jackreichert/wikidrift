"""LLM backend abstraction (provider-agnostic) — the cost lever for researchers.

Only two layers touch a model: L2 stance (stance.py) and L5 claim adjudication (l5_factcheck.py). Both
need exactly one primitive — send a prompt, get back JSON matching a strict JSON Schema. This module wraps
that primitive across providers so a researcher can pick a cheaper (or free/local) model:

  anthropic — default; native structured output (output_config json_schema). Preserves the validated results.
  openai    — OpenAI *and* any OpenAI-compatible base_url: OpenRouter / Together / Groq / DeepSeek / Fireworks
              (cheap hosted) and local Ollama / LM Studio / vLLM (free). The main cost lever. Uses
              response_format json_schema (strict) — our schemas are already strict-compatible.
  google    — native google-genai SDK; response_mime_type=application/json + the schema inlined in the prompt
              (Gemini's response_schema is an OpenAPI subset, not raw JSON Schema — inlining avoids translation).
  xai       — xAI Grok (alias: grok). OpenAI SDK + default base_url https://api.x.ai/v1 + XAI_API_KEY.
              Same strict json_schema path as openai (xAI is OpenAI-compatible).

Selection per field: explicit arg → env → default (see config.LLM_PROVIDER / DEFAULT_MODELS / KEY_ENV). The
openai and google SDKs are imported lazily, so offline commands and the test suite need neither installed.
complete_json() always returns a parsed dict.

Rate limits: every call goes through _send(), which retries 429 (rate limit) + 5xx with exponential backoff
(honoring a Retry-After header when present), so a free-tier limit pauses-and-continues instead of crashing
the run. 4xx client errors (400/401/403/404) are NOT retried — they surface immediately. Optional proactive
pacing via WIKIDRIFT_LLM_MIN_INTERVAL (seconds between calls); retry count via WIKIDRIFT_LLM_MAX_RETRIES.
"""
import json
import os
import sys
import time

from . import config

# Transient HTTP statuses worth retrying; 4xx client errors are deliberately excluded (they won't self-heal).
_RETRY_STATUSES = {429, 500, 502, 503, 504}
_DEFAULT_MAX_RETRIES = 5
_BASE_DELAY = 2.0    # seconds; doubles each attempt
_MAX_DELAY = 60.0    # cap on a single backoff wait

# Providers that speak the OpenAI chat.completions + response_format json_schema wire format.
_OPENAI_COMPAT = frozenset({"openai", "xai"})


def _resolve(provider, model, base_url, api_key):
    provider = (provider or os.environ.get("WIKIDRIFT_LLM_PROVIDER") or config.LLM_PROVIDER).lower()
    provider = config.PROVIDER_ALIASES.get(provider, provider)
    model = model or os.environ.get("WIKIDRIFT_LLM_MODEL") or config.DEFAULT_MODELS.get(provider)
    if not model:
        raise ValueError(f"no model for provider {provider!r}; pass --model or set WIKIDRIFT_LLM_MODEL")
    base_url = (base_url or os.environ.get("WIKIDRIFT_LLM_BASE_URL")
                or config.DEFAULT_BASE_URLS.get(provider))
    api_key = (api_key or os.environ.get("WIKIDRIFT_LLM_API_KEY")
               or os.environ.get(config.KEY_ENV.get(provider, "")))
    return provider, model, base_url, api_key


def _status_of(exc):
    """Best-effort HTTP status from an SDK exception (anthropic/openai `.status_code`, google `.code`)."""
    for attr in ("status_code", "code"):
        v = getattr(exc, attr, None)
        if isinstance(v, int):
            return v
    return None


def _looks_transient_conn(exc):
    """A status-less error that is a connection/timeout (worth retrying) vs a real bug (not)."""
    return any(w in type(exc).__name__ for w in ("Timeout", "Connection"))


def _retry_after(exc):
    """Seconds from a Retry-After header if the SDK exposes one, else None."""
    headers = getattr(getattr(exc, "response", None), "headers", None)
    if not headers:
        return None
    try:
        ra = headers.get("retry-after") or headers.get("Retry-After")
        return max(0.0, float(ra)) if ra else None
    except (AttributeError, TypeError, ValueError):
        return None


class Client:
    """Provider-agnostic LLM client. One method: complete_json(schema, prompt, max_tokens) -> dict.

    The underlying SDK client is built lazily on first call, so constructing a Client is keyless/importless.
    """

    def __init__(self, provider, model, base_url=None, api_key=None):
        self.provider, self.model, self.base_url, self.api_key = provider, model, base_url, api_key
        self._impl = None
        self.max_retries = int(os.environ.get("WIKIDRIFT_LLM_MAX_RETRIES", _DEFAULT_MAX_RETRIES))
        self.min_interval = float(os.environ.get("WIKIDRIFT_LLM_MIN_INTERVAL", 0) or 0)  # pacing, seconds
        self._last_call = 0.0

    def _client(self):
        if self._impl is not None:
            return self._impl
        if self.provider == "anthropic":
            import anthropic
            self._impl = anthropic.Anthropic(api_key=self.api_key) if self.api_key else anthropic.Anthropic()
        elif self.provider in _OPENAI_COMPAT:
            import openai
            kw = {}
            if self.api_key:
                kw["api_key"] = self.api_key
            if self.base_url:
                kw["base_url"] = self.base_url
            self._impl = openai.OpenAI(**kw)
        elif self.provider == "google":
            from google import genai
            self._impl = genai.Client(api_key=self.api_key) if self.api_key else genai.Client()
        else:
            raise ValueError(f"unknown LLM provider {self.provider!r} (anthropic|openai|google|xai)")
        return self._impl

    def _send(self, call):
        """Invoke `call()` with optional pacing + retry/backoff on rate-limit (429) and 5xx."""
        delay = _BASE_DELAY
        for attempt in range(self.max_retries + 1):
            if self.min_interval:                       # proactive pacing (stay under free-tier RPM)
                gap = self.min_interval - (time.monotonic() - self._last_call)
                if gap > 0:
                    time.sleep(gap)
            try:
                return call()
            except Exception as exc:                    # noqa: BLE001 — inspect; re-raise if not transient
                status = _status_of(exc)
                transient = status in _RETRY_STATUSES or (status is None and _looks_transient_conn(exc))
                if not transient or attempt == self.max_retries:
                    raise
                wait = _retry_after(exc) or min(_MAX_DELAY, delay)
                print(f"  [llm] {self.provider} {status or 'conn'} — retry "
                      f"{attempt + 1}/{self.max_retries} in {wait:.0f}s", file=sys.stderr)
                time.sleep(wait)
                delay *= 2
            finally:
                self._last_call = time.monotonic()

    def complete_json(self, schema, prompt, max_tokens=1024):
        """Send `prompt`, return a dict conforming to `schema` (a strict JSON Schema)."""
        # xai reuses the openai wire format; method name is the transport, not the brand.
        method = "openai" if self.provider in _OPENAI_COMPAT else self.provider
        return getattr(self, f"_{method}")(schema, prompt, max_tokens)

    def _anthropic(self, schema, prompt, max_tokens):
        # thinking disabled: these are structured JSON classifications, not reasoning tasks, and it matches
        # the validated Opus-4.8 baseline (which omits thinking → none). Without this, adaptive-thinking
        # models (Sonnet 5) spend the whole max_tokens budget thinking and emit no text block.
        resp = self._send(lambda: self._client().messages.create(
            model=self.model, max_tokens=max_tokens,
            thinking={"type": "disabled"},
            output_config={"format": {"type": "json_schema", "schema": schema}},
            messages=[{"role": "user", "content": prompt}]))
        text = next((b.text for b in resp.content if b.type == "text"), None)
        if not text:  # e.g. max_tokens hit before any text; clearer than a bare StopIteration
            raise RuntimeError(
                f"no text block from {self.model} (stop_reason={resp.stop_reason}); raise max_tokens")
        return json.loads(text)

    def _openai(self, schema, prompt, max_tokens):
        resp = self._send(lambda: self._client().chat.completions.create(
            model=self.model, max_tokens=max_tokens,
            response_format={"type": "json_schema",
                             "json_schema": {"name": "response", "schema": schema, "strict": True}},
            messages=[{"role": "user", "content": prompt}]))
        return json.loads(resp.choices[0].message.content)

    def _google(self, schema, prompt, max_tokens):
        instr = (prompt + "\n\nReturn ONLY a JSON object matching this JSON Schema "
                 "(no prose, no markdown fence):\n" + json.dumps(schema))
        resp = self._send(lambda: self._client().models.generate_content(
            model=self.model, contents=instr,
            config={"response_mime_type": "application/json", "max_output_tokens": max_tokens}))
        text = resp.text
        if not text:  # thinking models can spend the whole budget before emitting output → clear error
            fr = None
            try:
                fr = resp.candidates[0].finish_reason
            except (AttributeError, IndexError, TypeError):
                pass
            raise RuntimeError(
                f"empty response from {self.model} (finish_reason={fr}); raise max_tokens "
                "(Gemini thinking models consume the budget) or pick a lighter model")
        return json.loads(text)


def make_client(provider=None, model=None, base_url=None, api_key=None):
    """Build a provider-agnostic LLM client (arg → env → default for each field)."""
    return Client(*_resolve(provider, model, base_url, api_key))
