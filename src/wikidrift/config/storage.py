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

# The DuckDB of token facts. Kept at the historical spike location so the cached corpus
# (provenance.duckdb, ~350 MB of rsnap snapshots) is reused without a migration.
DATA_DIR = _REPO / ".planning" / "spikes" / "data"
DB = DATA_DIR / "provenance.duckdb"

# The Rust `snapshot-tokens` helper (tools/snapshot-tokens) emits per-token authorship for historical
# revisions. Build: `cargo build --release` in that dir.
SNAPSHOT_TOKENS_BIN = pathlib.Path(os.environ.get(
    "WIKIDRIFT_SNAPSHOT_TOKENS", _REPO / "tools" / "snapshot-tokens" / "target" / "release" / "snapshot-tokens"))
XML_CACHE = DATA_DIR / "history-xml"      # cached full-history MediaWiki export XML (immutable per article)

# Canonical per-article findings JSON the viewer reads, so a production run on a NEW article flows
# straight to the site (the frozen spike out/ dirs remain the historical record).
FINDINGS = DATA_DIR / "findings"
