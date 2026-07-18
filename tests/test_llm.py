"""Unit tests for the LLM backend abstraction (no network, no keys, no SDKs installed).

Each backend's request shape + JSON parsing is verified by injecting a fake SDK client as `_impl`, so
`_client()` returns it without importing anthropic/openai/google-genai.
"""
import os
import pathlib
import tempfile
import types
import unittest

from wikidrift import config, llm


class _Rec:
    """Records the last create()/generate_content() kwargs and returns a canned response."""
    def __init__(self, response):
        self.response = response
        self.kwargs = None

    def __call__(self, **kwargs):
        self.kwargs = kwargs
        return self.response


def _anthropic_impl(rec):
    block = types.SimpleNamespace(type="text", text='{"ok": 1}')
    rec.response = types.SimpleNamespace(
        content=[block], usage=types.SimpleNamespace(input_tokens=10, output_tokens=4))
    return types.SimpleNamespace(messages=types.SimpleNamespace(create=rec))


def _openai_impl(rec):
    msg = types.SimpleNamespace(message=types.SimpleNamespace(content='{"ok": 2}'))
    rec.response = types.SimpleNamespace(
        choices=[msg], usage=types.SimpleNamespace(prompt_tokens=12, completion_tokens=5))
    return types.SimpleNamespace(chat=types.SimpleNamespace(completions=types.SimpleNamespace(create=rec)))


def _google_impl(rec):
    rec.response = types.SimpleNamespace(
        text='{"ok": 3}',
        usage_metadata=types.SimpleNamespace(prompt_token_count=14, candidates_token_count=6),
    )
    return types.SimpleNamespace(models=types.SimpleNamespace(generate_content=rec))


SCHEMA = {"type": "object", "additionalProperties": False, "required": ["ok"],
          "properties": {"ok": {"type": "integer"}}}


class Resolution(unittest.TestCase):
    def setUp(self):
        # isolate from any ambient WIKIDRIFT_LLM_* env
        self._saved = {k: os.environ.pop(k, None)
                       for k in ("WIKIDRIFT_LLM_PROVIDER", "WIKIDRIFT_LLM_MODEL", "WIKIDRIFT_LLM_BASE_URL",
                                 "WIKIDRIFT_LLM_PROVIDER_PRIORITY", "WIKIDRIFT_LLM_API_KEY",
                                 "ANTHROPIC_API_KEY", "OPENAI_API_KEY", "XAI_API_KEY", "GOOGLE_API_KEY")}

    def tearDown(self):
        for k, v in self._saved.items():
            if v is not None:
                os.environ[k] = v
            else:
                os.environ.pop(k, None)

    def test_default_is_anthropic_sonnet(self):
        # default provider stays Anthropic; default model is Sonnet 5 (S08 operator choice). Opus 4.8 remains
        # the certification baseline, selectable via --model — see config.DEFAULT_MODELS.
        c = llm.make_client()
        self.assertEqual(c.provider, "anthropic")
        self.assertEqual(c.model, "claude-sonnet-5")

    def test_args_override_everything(self):
        c = llm.make_client("openai", "some-model", "https://openrouter.ai/api/v1")
        self.assertEqual((c.provider, c.model, c.base_url), ("openai", "some-model", "https://openrouter.ai/api/v1"))

    def test_provider_default_model(self):
        self.assertEqual(llm.make_client("openai").model, "gpt-4o-mini")
        self.assertEqual(llm.make_client("grok").model, "grok-4")
        self.assertEqual(llm.make_client("google").model, "gemini-flash-lite-latest")
        self.assertEqual(llm.make_client("xai").model, "grok-4")

    def test_xai_default_base_url(self):
        c = llm.make_client("xai")
        self.assertEqual(c.provider, "xai")
        self.assertEqual(c.base_url, "https://api.x.ai/v1")

    def test_grok_alias_resolves_to_xai(self):
        c = llm.make_client("grok")
        self.assertEqual(c.provider, "xai")
        self.assertEqual(c.model, "grok-4")
        self.assertEqual(c.base_url, "https://api.x.ai/v1")

    def test_auto_priority_uses_first_configured_key(self):
        # In auto mode (no explicit provider), choose the first provider in priority with a configured key.
        os.environ["WIKIDRIFT_LLM_PROVIDER_PRIORITY"] = "anthropic,openai,google"
        os.environ["OPENAI_API_KEY"] = "x"
        try:
            c = llm.make_client()
            self.assertEqual(c.provider, "openai")
        finally:
            os.environ.pop("OPENAI_API_KEY", None)

    def test_env_selects_provider(self):
        os.environ["WIKIDRIFT_LLM_PROVIDER"] = "openai"
        self.assertEqual(llm.make_client().provider, "openai")

    def test_env_grok_alias(self):
        os.environ["WIKIDRIFT_LLM_PROVIDER"] = "grok"
        self.assertEqual(llm.make_client().provider, "xai")

    def test_unknown_provider_without_model_raises(self):
        with self.assertRaises(ValueError):
            llm.make_client("mystery")


class Backends(unittest.TestCase):
    def _client(self, provider, impl_factory):
        rec = _Rec(None)
        c = llm.make_client(provider, "m")
        c._impl = impl_factory(rec)
        return c, rec

    def test_anthropic_shape_and_parse(self):
        c, rec = self._client("anthropic", _anthropic_impl)
        self.assertEqual(c.complete_json(SCHEMA, "hi", max_tokens=64), {"ok": 1})
        self.assertEqual(rec.kwargs["output_config"], {"format": {"type": "json_schema", "schema": SCHEMA}})
        self.assertEqual(rec.kwargs["max_tokens"], 64)
        self.assertEqual(c.usage_records[0]["input_tokens"], 10)

    def test_openai_shape_and_parse(self):
        c, rec = self._client("openai", _openai_impl)
        self.assertEqual(c.complete_json(SCHEMA, "hi"), {"ok": 2})
        rf = rec.kwargs["response_format"]
        self.assertEqual(rf["type"], "json_schema")
        self.assertTrue(rf["json_schema"]["strict"])
        self.assertEqual(rf["json_schema"]["schema"], SCHEMA)
        self.assertEqual(c.usage_records[0]["output_tokens"], 5)

    def test_xai_reuses_openai_wire_format(self):
        # Grok is OpenAI-compatible: same chat.completions + json_schema response_format path.
        c, rec = self._client("xai", _openai_impl)
        self.assertEqual(c.complete_json(SCHEMA, "hi"), {"ok": 2})
        rf = rec.kwargs["response_format"]
        self.assertEqual(rf["type"], "json_schema")
        self.assertTrue(rf["json_schema"]["strict"])

    def test_google_shape_and_parse(self):
        c, rec = self._client("google", _google_impl)
        self.assertEqual(c.complete_json(SCHEMA, "hi"), {"ok": 3})
        self.assertEqual(rec.kwargs["config"]["response_mime_type"], "application/json")
        self.assertIn('"ok"', rec.kwargs["contents"])  # schema inlined into the prompt
        self.assertEqual(c.usage_records[0]["input_tokens"], 14)

    def test_usage_summary_applies_explicit_model_pricing(self):
        previous = os.environ.get("WIKIDRIFT_LLM_PRICING_JSON")
        os.environ["WIKIDRIFT_LLM_PRICING_JSON"] = (
            '{"anthropic:m":{"input_per_million":3,"output_per_million":15}}'
        )
        try:
            c, _ = self._client("anthropic", _anthropic_impl)
            checkpoint = llm.usage_checkpoint(c)
            c.complete_json(SCHEMA, "hi")
            summary = llm.usage_summary(c, checkpoint)
        finally:
            if previous is None:
                os.environ.pop("WIKIDRIFT_LLM_PRICING_JSON", None)
            else:
                os.environ["WIKIDRIFT_LLM_PRICING_JSON"] = previous
        self.assertEqual(summary["calls"], 1)
        self.assertEqual(summary["input_tokens"], 10)
        self.assertEqual(summary["output_tokens"], 4)
        self.assertEqual(summary["estimated_usd"], 0.00009)
        self.assertTrue(summary["all_calls_priced"])
        self.assertEqual(summary["records"][0]["pricing_usd_per_million"], {
            "input": 3.0, "output": 15.0,
        })

    def test_usage_summary_does_not_present_missing_pricing_as_zero(self):
        c, _ = self._client("openai", _openai_impl)
        c.complete_json(SCHEMA, "hi")
        summary = llm.usage_summary(c)
        self.assertIsNone(summary["estimated_usd"])
        self.assertFalse(summary["all_calls_priced"])

    def test_truncated_json_retries_once_with_double_output_budget(self):
        responses = [
            types.SimpleNamespace(
                content=[types.SimpleNamespace(type="text", text='{"ok":')],
                usage=types.SimpleNamespace(input_tokens=10, output_tokens=64),
            ),
            types.SimpleNamespace(
                content=[types.SimpleNamespace(type="text", text='{"ok": 1}')],
                usage=types.SimpleNamespace(input_tokens=10, output_tokens=4),
            ),
        ]
        calls = []

        def create(**kwargs):
            calls.append(kwargs)
            return responses.pop(0)

        client = llm.make_client("anthropic", "m")
        client._impl = types.SimpleNamespace(messages=types.SimpleNamespace(create=create))

        self.assertEqual(client.complete_json(SCHEMA, "hi", max_tokens=64), {"ok": 1})
        self.assertEqual([call["max_tokens"] for call in calls], [64, 128])
        self.assertEqual(len(client.usage_records), 2)
        self.assertEqual(llm.usage_summary(client)["output_tokens"], 68)

    def test_google_empty_output_raises_clear_error(self):
        # thinking models can spend the whole token budget → empty text; must be an actionable error,
        # not a cryptic json.loads("") failure.
        rec = _Rec(types.SimpleNamespace(text="", candidates=[]))
        c = llm.make_client("google", "m")
        c._impl = types.SimpleNamespace(models=types.SimpleNamespace(generate_content=rec))
        with self.assertRaises(RuntimeError) as cm:
            c.complete_json(SCHEMA, "hi")
        self.assertIn("empty response", str(cm.exception))


class _Boom(Exception):
    """Fake SDK error carrying an HTTP status (like anthropic/openai `.status_code`)."""
    def __init__(self, status):
        super().__init__(f"boom {status}")
        self.status_code = status


class Retry(unittest.TestCase):
    def setUp(self):
        self._sleep = llm.time.sleep
        llm.time.sleep = lambda *a, **k: None      # no real waiting in tests

    def tearDown(self):
        llm.time.sleep = self._sleep

    def test_retries_then_succeeds(self):
        c = llm.make_client("anthropic", "m")
        n = {"i": 0}

        def flaky():
            n["i"] += 1
            if n["i"] < 3:
                raise _Boom(429)
            return "ok"

        self.assertEqual(c._send(flaky), "ok")
        self.assertEqual(n["i"], 3)                # two 429s survived, third call returned

    def test_non_retryable_status_raises_immediately(self):
        c = llm.make_client("anthropic", "m")
        n = {"i": 0}

        def bad():
            n["i"] += 1
            raise _Boom(400)                       # client error — must not retry

        with self.assertRaises(_Boom):
            c._send(bad)
        self.assertEqual(n["i"], 1)

    def test_exhausts_retries_then_raises(self):
        c = llm.make_client("anthropic", "m")
        c.max_retries = 2
        n = {"i": 0}

        def always():
            n["i"] += 1
            raise _Boom(429)

        with self.assertRaises(_Boom):
            c._send(always)
        self.assertEqual(n["i"], 3)                # initial + 2 retries

    def test_retry_after_header_is_honored(self):
        waits = []
        llm.time.sleep = lambda s, *a, **k: waits.append(s)
        c = llm.make_client("anthropic", "m")
        n = {"i": 0}

        class _RA(Exception):
            status_code = 429
            response = types.SimpleNamespace(headers={"retry-after": "7"})

        def flaky():
            n["i"] += 1
            if n["i"] < 2:
                raise _RA()
            return "ok"

        self.assertEqual(c._send(flaky), "ok")
        self.assertIn(7.0, waits)                  # used the header, not the exponential default


class Failover(unittest.TestCase):
    def test_rotates_to_next_provider_on_429(self):
        first = llm.Client("anthropic", "m1")
        second = llm.Client("openai", "m2")

        def boom(*_a, **_k):
            raise _Boom(429)

        first.complete_json = boom
        second.complete_json = lambda *_a, **_k: {"ok": 9}

        fc = llm.FailoverClient([first, second])
        out = fc.complete_json(SCHEMA, "hi")
        self.assertEqual(out, {"ok": 9})
        self.assertEqual(fc.provider, "openai")

    def test_usage_is_attributed_once_to_successful_failover_provider(self):
        first = llm.Client("anthropic", "m1")
        second = llm.Client("openai", "m2")

        def boom(*_args, **_kwargs):
            raise _Boom(429)

        def succeed(*_args, **_kwargs):
            second.usage_records.append({
                "provider": "openai", "model": "m2", "input_tokens": 12,
                "output_tokens": 5, "estimated_usd": None, "pricing_key": None,
                "pricing_usd_per_million": None,
            })
            return {"ok": 9}

        first.complete_json = boom
        second.complete_json = succeed
        client = llm.FailoverClient([first, second])

        self.assertEqual(client.complete_json(SCHEMA, "hi"), {"ok": 9})
        self.assertEqual(len(client.usage_records), 1)
        self.assertEqual(client.usage_records[0]["provider"], "openai")


class DotEnv(unittest.TestCase):
    def _write(self, body):
        d = tempfile.mkdtemp()
        p = pathlib.Path(d) / ".env"
        p.write_text(body, encoding="utf-8")
        return p

    def test_sets_new_var_skips_comments_and_quotes(self):
        key = "WIKIDRIFT_TEST_DOTENV_A"
        os.environ.pop(key, None)
        try:
            config._load_dotenv(self._write(f'# a comment\n\nexport {key}="hello"\n'))
            self.assertEqual(os.environ.get(key), "hello")
        finally:
            os.environ.pop(key, None)

    def test_does_not_override_existing(self):
        key = "WIKIDRIFT_TEST_DOTENV_B"
        os.environ[key] = "already"
        try:
            config._load_dotenv(self._write(f"{key}=fromfile\n"))
            self.assertEqual(os.environ[key], "already")  # explicit env wins
        finally:
            os.environ.pop(key, None)

    def test_missing_file_is_noop(self):
        config._load_dotenv(pathlib.Path(tempfile.mkdtemp()) / "nope.env")  # no raise


if __name__ == "__main__":
    unittest.main()
