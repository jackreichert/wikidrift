"""Shared configuration, split by axis-of-change behind this facade so every `config.X` and
`from .config import X` call site is unchanged.

Grouped by how often each part changes, not by theme (Clean Architecture ch.14 — keep the volatile
`thresholds` apart from the stable endpoints/paths/provider tables). `storage` is imported first: it loads
the repo-root .env before `providers` reads WIKIDRIFT_LLM_* / provider keys from the environment.
"""
from .storage import DATA_DIR, DB, SNAPSHOT_TOKENS_BIN, XML_CACHE, FINDINGS, _load_dotenv  # noqa: F401
from .http import UA, WIKIWHO, ACTION, WIKIDATA, action, session, get_json_retrying  # noqa: F401
from .parsing import ANON_IP_RE, slugify, citation_domains  # noqa: F401
from .findings_store import write_findings, load_findings  # noqa: F401
from .thresholds import (  # noqa: F401
    MIN_COHORT, MIN_MATURE, MAG_FLOOR, CONFIRM_DROP, CREEP_MEAN, DURABLE_Q, RECENT_YEARS, ELEVATED,
    BIN_DAYS, MIN_REVS, SMOOTH_K, LEAD_FLOOR, ANOMALY_MIN, GROWTH_RATIO, CHURN_ANOMALY, CHURN_MIN_BYTES,
    MASS_FLOOR, SLOW_BLEED_FLOOR)
from .providers import LLM_PROVIDER, LLM_PROVIDER_PRIORITY, DEFAULT_MODELS, KEY_ENV, MODEL  # noqa: F401

__all__ = [
    "DATA_DIR", "DB", "SNAPSHOT_TOKENS_BIN", "XML_CACHE", "FINDINGS", "_load_dotenv",
    "UA", "WIKIWHO", "ACTION", "WIKIDATA", "action", "session", "get_json_retrying",
    "ANON_IP_RE", "slugify", "citation_domains",
    "write_findings", "load_findings",
    "MIN_COHORT", "MIN_MATURE", "MAG_FLOOR", "CONFIRM_DROP", "CREEP_MEAN", "DURABLE_Q", "RECENT_YEARS",
    "ELEVATED", "BIN_DAYS", "MIN_REVS", "SMOOTH_K", "LEAD_FLOOR", "ANOMALY_MIN", "GROWTH_RATIO",
    "CHURN_ANOMALY", "CHURN_MIN_BYTES", "MASS_FLOOR", "SLOW_BLEED_FLOOR",
    "LLM_PROVIDER", "LLM_PROVIDER_PRIORITY", "DEFAULT_MODELS", "KEY_ENV", "MODEL",
]
