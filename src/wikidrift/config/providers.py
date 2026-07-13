"""LLM provider tables (L2 stance + L5 claim adjudication). See llm.py for the client.

LLM-agnostic: default provider is Anthropic; default model is Sonnet 5 (operator choice, Session 08) —
near-Opus quality on the nuanced NPOV / cross-lingual-fact work at ~40-60% of Opus cost. The ★#3 benchmark
was CERTIFIED on claude-opus-4-8 (the reproduce-the-certification baseline, selectable with --model). A
researcher can pick a cheaper/local backend via env or --provider/--model/--base-url; the openai path takes
a base_url, reaching OpenAI-compatible endpoints (OpenRouter/Together/Groq/DeepSeek; local Ollama/vLLM).

Reads WIKIDRIFT_LLM_PROVIDER from the environment — storage's .env load runs first (facade import order).
"""
import os

LLM_PROVIDER = os.environ.get("WIKIDRIFT_LLM_PROVIDER", "anthropic")
LLM_PROVIDER_PRIORITY = os.environ.get("WIKIDRIFT_LLM_PROVIDER_PRIORITY", "anthropic,openai,grok,google")
DEFAULT_MODELS = {
    "anthropic": "claude-sonnet-5",       # default (S08 operator choice); Opus 4.8 = certification baseline (--model)
    "openai": "gpt-4o-mini",              # cheap; supports strict json_schema structured output
    "grok": "grok-3-mini",               # xAI Grok via the OpenAI-compatible API
    "google": "gemini-flash-lite-latest",  # cheap + stable alias; avoids the 2.0-flash quota-0 / 2.5 deprecation traps
}
# API-key env var checked per provider (WIKIDRIFT_LLM_API_KEY overrides all).
KEY_ENV = {
    "anthropic": "ANTHROPIC_API_KEY",
    "openai": "OPENAI_API_KEY",
    "grok": "XAI_API_KEY",
    "google": "GOOGLE_API_KEY",
}

MODEL = DEFAULT_MODELS["anthropic"]   # back-compat alias, tracks the Anthropic default (now Sonnet 5)
