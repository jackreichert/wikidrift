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
    rec.response = types.SimpleNamespace(content=[block])
    return types.SimpleNamespace(messages=types.SimpleNamespace(create=rec))


def _openai_impl(rec):
    msg = types.SimpleNamespace(message=types.SimpleNamespace(content='{"ok": 2}'))
    rec.response = types.SimpleNamespace(choices=[msg])
    return types.SimpleNamespace(chat=types.SimpleNamespace(completions=types.SimpleNamespace(create=rec)))


def _google_impl(rec):
    rec.response = types.SimpleNamespace(text='{"ok": 3}')
    return types.SimpleNamespace(models=types.SimpleNamespace(generate_content=rec))


SCHEMA = {"type": "object", "additionalProperties": False, "required": ["ok"],
          "properties": {"ok": {"type": "integer"}}}


class Resolution(unittest.TestCase):
    def setUp(self):
        # isolate from any ambient WIKIDRIFT_LLM_* env
        self._saved = {k: os.environ.pop(k, None)
                       for k in ("WIKIDRIFT_LLM_PROVIDER", "WIKIDRIFT_LLM_MODEL", "WIKIDRIFT_LLM_BASE_URL")}

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
        self.assertEqual(llm.make_client("google").model, "gemini-flash-lite-latest")

    def test_env_selects_provider(self):
        os.environ["WIKIDRIFT_LLM_PROVIDER"] = "openai"
        self.assertEqual(llm.make_client().provider, "openai")

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

    def test_openai_shape_and_parse(self):
        c, rec = self._client("openai", _openai_impl)
        self.assertEqual(c.complete_json(SCHEMA, "hi"), {"ok": 2})
        rf = rec.kwargs["response_format"]
        self.assertEqual(rf["type"], "json_schema")
        self.assertTrue(rf["json_schema"]["strict"])
        self.assertEqual(rf["json_schema"]["schema"], SCHEMA)

    def test_google_shape_and_parse(self):
        c, rec = self._client("google", _google_impl)
        self.assertEqual(c.complete_json(SCHEMA, "hi"), {"ok": 3})
        self.assertEqual(rec.kwargs["config"]["response_mime_type"], "application/json")
        self.assertIn('"ok"', rec.kwargs["contents"])  # schema inlined into the prompt

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
