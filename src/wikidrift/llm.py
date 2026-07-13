"""LLM backend abstraction (provider-agnostic) — the cost lever for researchers.

Only two layers touch a model: L2 stance (stance.py) and L5 claim adjudication (l5_factcheck.py). Both
need exactly one primitive — send a prompt, get back JSON matching a strict JSON Schema. This module wraps
that primitive across providers so a researcher can pick a cheaper (or free/local) model:

  anthropic — default; native structured output (output_config json_schema). Preserves the validated results.
  openai    — OpenAI *and* any OpenAI-compatible base_url: OpenRouter / Together / Groq / DeepSeek / Fireworks
              (cheap hosted) and local Ollama / LM Studio / vLLM (free). The main cost lever. Uses
              response_format json_schema (strict) — our schemas are already strict-compatible.
    grok      — xAI Grok via the same OpenAI-compatible path (default base URL https://api.x.ai/v1).
  google    — native google-genai SDK; response_mime_type=application/json + the schema inlined in the prompt
              (Gemini's response_schema is an OpenAPI subset, not raw JSON Schema — inlining avoids translation).

Selection per field: explicit arg → env → default (see config.LLM_PROVIDER / DEFAULT_MODELS / KEY_ENV). The
openai and google SDKs are imported lazily, so offline commands and the test suite need neither installed.
complete_json() always returns a parsed dict.

When no provider is explicitly selected (arg or WIKIDRIFT_LLM_PROVIDER), auto mode uses
WIKIDRIFT_LLM_PROVIDER_PRIORITY and available provider keys; on quota/rate-limit exhaustion it fails over
to the next configured provider.

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


def _canonical_provider(provider):
    """Normalize provider aliases to a single internal identifier."""
    p = (provider or "").lower().strip()
    return "xai" if p == "grok" else p


def _priority_chain():
    """Ordered provider chain used for auto-failover when provider is not explicitly selected."""
    raw = os.environ.get("WIKIDRIFT_LLM_PROVIDER_PRIORITY", config.LLM_PROVIDER_PRIORITY)
    ordered = []
    seen = set()
    for p in (_canonical_provider(x.strip().lower()) for x in raw.split(",")):
        if p and p in config.DEFAULT_MODELS and p not in seen:
            ordered.append(p)
            seen.add(p)
    return ordered or ["anthropic", "openai", "xai", "google"]


def _provider_base_url(provider, base_url=None):
    provider = _canonical_provider(provider)
    if base_url:
        return base_url
    if provider == "xai":
        return os.environ.get("WIKIDRIFT_LLM_GROK_BASE_URL", "https://api.x.ai/v1")
    return os.environ.get("WIKIDRIFT_LLM_BASE_URL")


def _provider_model(provider, model=None):
    provider = _canonical_provider(provider)
    if model:
        return model
    per_provider = os.environ.get(f"WIKIDRIFT_LLM_MODEL_{provider.upper()}")
    if per_provider:
        return per_provider
    return os.environ.get("WIKIDRIFT_LLM_MODEL") or config.DEFAULT_MODELS.get(provider)


def _provider_key(provider, api_key=None):
    provider = _canonical_provider(provider)
    return api_key or os.environ.get("WIKIDRIFT_LLM_API_KEY") or os.environ.get(config.KEY_ENV.get(provider, ""))


def _resolve(provider, model, base_url, api_key):
    provider = _canonical_provider(provider or os.environ.get("WIKIDRIFT_LLM_PROVIDER") or config.LLM_PROVIDER)
    model = _provider_model(provider, model)
    if not model:
        raise ValueError(f"no model for provider {provider!r}; pass --model or set WIKIDRIFT_LLM_MODEL")
    base_url = _provider_base_url(provider, base_url)
    api_key = _provider_key(provider, api_key)
    return provider, model, base_url, api_key


def _quota_or_rate_limited(exc):
    """Whether this exception should trigger provider failover (after in-provider retries)."""
    status = _status_of(exc)
    text = str(exc).lower()
    return status == 429 or any(tok in text for tok in (
        "insufficient_quota", "quota", "rate limit", "rate_limit", "tokens per", "billing"
    ))


class FailoverClient:
    """Thin wrapper that rotates providers when a provider is rate/quota exhausted."""

    def __init__(self, clients):
        if not clients:
            raise ValueError("FailoverClient needs at least one provider client")
        self._clients = clients
        self._idx = 0
        self.provider = clients[0].provider
        self.model = clients[0].model
        self.base_url = clients[0].base_url

    def complete_json(self, schema, prompt, max_tokens=1024):
        last = None
        for hop in range(len(self._clients)):
            i = (self._idx + hop) % len(self._clients)
            c = self._clients[i]
            try:
                out = c.complete_json(schema, prompt, max_tokens=max_tokens)
                self._idx = i
                self.provider, self.model, self.base_url = c.provider, c.model, c.base_url
                return out
            except Exception as exc:  # noqa: BLE001 — pass through unless failover-eligible
                last = exc
                if hop == len(self._clients) - 1 or not _quota_or_rate_limited(exc):
                    raise
                nxt = self._clients[(i + 1) % len(self._clients)]
                print(f"  [llm] failover {c.provider} -> {nxt.provider} (quota/rate-limit)", file=sys.stderr)
        raise last  # pragma: no cover


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
        elif self.provider in ("openai", "xai"):
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
            raise ValueError(f"unknown LLM provider {self.provider!r} (anthropic|openai|xai|google)")
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
        if self.provider in ("openai", "xai"):
            return self._openai(schema, prompt, max_tokens)
        return getattr(self, f"_{self.provider}")(schema, prompt, max_tokens)

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
    """Build a provider-agnostic LLM client (arg → env → default for each field).

    If provider is explicitly selected (arg or WIKIDRIFT_LLM_PROVIDER), use that provider only.
    Otherwise, build a key-aware priority chain from WIKIDRIFT_LLM_PROVIDER_PRIORITY and fail over on
    quota/rate-limit exhaustion.
    """
    explicit = provider or os.environ.get("WIKIDRIFT_LLM_PROVIDER")
    if explicit:
        return Client(*_resolve(provider, model, base_url, api_key))

    chain = []
    for p in _priority_chain():
        k = _provider_key(p, api_key)
        if not k:
            continue
        m = _provider_model(p, model)
        b = _provider_base_url(p, base_url)
        if m:
            chain.append(Client(p, m, b, k))

    if not chain:
        # No provider-specific key found; keep legacy default resolution behavior.
        return Client(*_resolve(provider, model, base_url, api_key))
    if len(chain) == 1:
        return chain[0]
    return FailoverClient(chain)
