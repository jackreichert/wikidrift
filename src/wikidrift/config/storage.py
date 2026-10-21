"""Storage paths + .env auto-load — the stable filesystem layer of config.

Imported first by the package facade so the repo-root .env is loaded into the environment BEFORE
`providers` reads WIKIDRIFT_LLM_* / provider keys.
"""
import os
import pathlib


# --- .env auto-load (dependency-free) ---------------------------------------
# Load KEY=VALUE pairs from the repo-root .env into the environment so a researcher's provider key
# (GOOGLE_API_KEY / OPENAI_API_KEY / ANTHROPIC_API_KEY / XAI_API_KEY) or WIKIDRIFT_LLM_* is picked up without a manual
# `source`. NEVER overrides an already-set env var (an explicit export or CI secret always wins) and never
# prints values. .env is gitignored; see .env.example for the recognized names.
def _load_dotenv(path):
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.removeprefix("export ").strip()
        if key and key not in os.environ:
            os.environ[key] = val.strip().strip('"').strip("'")


# config/ is one level deeper than the old config.py, so the repo root is parents[3] (was parents[2]).
_REPO = pathlib.Path(__file__).resolve().parents[3]
_load_dotenv(_REPO / ".env")

# The DuckDB of token facts. The historical spike location remains the default; workers can point
# WIKIDRIFT_DATA_DIR at an article-owned shard to avoid sharing a DuckDB writer.
DATA_DIR = pathlib.Path(os.environ.get(
    "WIKIDRIFT_DATA_DIR", _REPO / ".planning" / "spikes" / "data"
)).expanduser()
DB = DATA_DIR / "provenance.duckdb"
ARTICLES_DIR = DATA_DIR / "articles"


def missing_corpus_db_message(db: pathlib.Path | None = None) -> str:
    """Human-readable guidance when provenance.duckdb is absent for the active DATA_DIR."""
    path = pathlib.Path(db) if db is not None else DB
    return (
        f"token corpus not found: {path}\n"
        "Need a local provenance.duckdb for this WIKIDRIFT_DATA_DIR before analyze/pipeline.\n"
        "Fix: `uv run wikidrift migrate-shards` (or let cover_missing_topics rematerialize from the "
        "canonical corpus), restore `{slug}/shard.duckdb.zst` from R2 into provenance.duckdb, "
        "bootstrap/ingest the article, or unset WIKIDRIFT_DATA_DIR to use the canonical data dir."
    )


def require_corpus_db(db: pathlib.Path | None = None) -> pathlib.Path:
    """Return the corpus path, or raise FileNotFoundError with migrate/bootstrap guidance."""
    path = pathlib.Path(db) if db is not None else DB
    if not path.is_file():
        raise FileNotFoundError(missing_corpus_db_message(path))
    return path

# The Rust `snapshot-tokens` helper (tools/snapshot-tokens) emits per-token authorship for historical
# revisions. Build: `cargo build --release` in that dir.
SNAPSHOT_TOKENS_BIN = pathlib.Path(os.environ.get(
    "WIKIDRIFT_SNAPSHOT_TOKENS", _REPO / "tools" / "snapshot-tokens" / "target" / "release" / "snapshot-tokens"))
XML_CACHE = DATA_DIR / "history-xml"      # cached full-history MediaWiki export XML (immutable per article)

# Canonical per-article findings JSON the viewer reads, so a production run on a NEW article flows
# straight to the site (the frozen spike out/ dirs remain the historical record).
FINDINGS = DATA_DIR / "findings"
