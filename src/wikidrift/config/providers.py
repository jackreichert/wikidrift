"""LLM provider tables (L2 stance + L5 claim adjudication). See llm.py for the client.

LLM-agnostic: default provider is Anthropic; default model is Sonnet 5 (operator choice, Session 08) —
near-Opus quality on the nuanced NPOV / cross-lingual-fact work at ~40-60% of Opus cost. The ★#3 benchmark
was CERTIFIED on claude-opus-4-8 (the reproduce-the-certification baseline, selectable with --model). A
researcher can pick a cheaper/local backend via env or --provider/--model/--base-url; the openai path takes
a base_url, reaching OpenAI-compatible endpoints (OpenRouter/Together/Groq/DeepSeek; local Ollama/vLLM).
`xai` (alias `grok`) is first-class: OpenAI SDK + https://api.x.ai/v1 + XAI_API_KEY.

Reads WIKIDRIFT_LLM_PROVIDER from the environment — storage's .env load runs first (facade import order).
"""
import os

LLM_PROVIDER = os.environ.get("WIKIDRIFT_LLM_PROVIDER", "anthropic")
DEFAULT_MODELS = {
    "anthropic": "claude-sonnet-5",       # default (S08 operator choice); Opus 4.8 = certification baseline (--model)
    "openai": "gpt-4o-mini",              # cheap; supports strict json_schema structured output
    "google": "gemini-flash-lite-latest",  # cheap + stable alias; avoids the 2.0-flash quota-0 / 2.5 deprecation traps
    "xai": "grok-4",                      # xAI Grok via OpenAI-compatible API (json_schema structured output)
}
# API-key env var checked per provider (WIKIDRIFT_LLM_API_KEY overrides all).
KEY_ENV = {
    "anthropic": "ANTHROPIC_API_KEY",
    "openai": "OPENAI_API_KEY",
    "google": "GOOGLE_API_KEY",
    "xai": "XAI_API_KEY",
}
# Default base URL for OpenAI-compatible providers that are not api.openai.com.
DEFAULT_BASE_URLS = {
    "xai": "https://api.x.ai/v1",
}
# CLI / env aliases → canonical provider id (e.g. --provider grok → xai).
PROVIDER_ALIASES = {
    "grok": "xai",
}

MODEL = DEFAULT_MODELS["anthropic"]   # back-compat alias, tracks the Anthropic default (now Sonnet 5)
