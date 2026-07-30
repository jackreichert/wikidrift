# wikidrift

Editor-agnostic, temporal **narrative-drift detector** for Wikipedia. Reads an article against its *own* edit history to surface where a long-stable narrative was rewritten, and attributes the public edits — from public data only. Every output is a **lead for a researcher, never a published verdict**. It is a *change* detector, not a *bias* detector (the base-rate finding); discriminating benign change from capture is the job of the L2/L5 layers.

Promoted from `.planning/spikes/` (001a/b, 005, 008, 009, 010), which remain the frozen experimental record.
Full design + methodology live in the vault:
`~/Documents/JackObsidian/encyclopediae/wikipedia-filter-mirror-design.md`.

## Layout

| Module | Role | Spike origin |
| --- | --- | --- |
| `config.py` | Paths, endpoints, HTTP session, all tuned thresholds | (was duplicated everywhere) |
| `provenance.py` | DuckDB schema + WikiWho/Action-API fetching, persistent snapshots | 001a, 005 |
| `drift.py` | **L1** PWR engine: primary interval episodes → rolling fallback candidates → binary-search confirm → attribution; offline `verdict_dict`; descriptive `profile` (recency + editor concentration) | 005, 002 |
| `prerank.py` | Metadata-only candidate pre-ranker (`removal→PWR` / `addition→L2`) | 008 |
| `stance.py` | **L2** LLM stance classifier (NPOV axis, not sentiment) | 010 |
| `benchmark.py` | Adjudicated ground-truth roster + scoring | 009 |
| `l5_crosslingual.py` | **L5 cross-language stance comparison** (static + pivot-relative stance spread) | 012a/b/c |
| `l5_framing_lite.py` | **L5 cross-language lead comparison** using an exact confirmed L1 pair when fresh, with candidate/static fallbacks and oldid receipts | S09 |
| `l5_factcheck.py` | **L5 #2** cross-edition citation + claim (fact) divergence, as-of aware | 014 |
| `mscore.py` | Yasseri mutual-revert controversy corroborator (metadata-only) | 013 |
| `ingest.py` | **Local `wikiwho_rs`-on-dumps** rsnap ingestion — coverage gaps + corpus-scale batch (Rust `tools/snapshot-tokens` helper) | 011 |
| `l4.py` | **L4** graph-guided discovery: seed from editors attributed with removals in a confirmed article → their removal footprint (a *lead*) → independent L1 re-test of each fresh candidate | S08 |
| `l5_sources.py` | **L5 #3b** citation-source composition change (reference-agnostic): what the article's own citations changed *from → to* across the L1 pivot; rates no source | S08 |
| `bootstrap.py` | Populate the token corpus (`provenance.duckdb`) for a slate (default: the roster), sequential single-writer — the onboarding/rebuild path | 007 |
| `cli.py` | `wikidrift` command dispatch | — |

> **L5 (external-reference bias) — promoted (spikes 012/013/014).** Two instruments cover the
> born-biased / long-stable bias the internal engine is blind to: `l5_crosslingual` (framing capture —
> cross-lingual NPOV divergence, static + pivot-relative) and `l5_factcheck` (fact distortion —
> cross-edition claim + citation divergence, as-of aware; closes the KL Warschau gap). `mscore` is a
> controversy corroborator (low/zero M on a flagged article ⇒ route-to-L5). Instrument #3
> (cross-encyclopedia vs Britannica / scholarly corpus) is future. Downstream: an open-source,
> GitHub-style diff viewer (pivot-history timeline, "blame" overlay, framing + fact panels) at
> `drift.encyclopediae.org`. See `.planning/spikes/MANIFEST.md` and the vault design doc §0.1 / §6A.

## Usage

```bash
uv run wikidrift bootstrap            # populate the token corpus for the roster (fetch; supersedes spike 007)
uv run wikidrift benchmark            # score the adjudicated roster (offline)
uv run wikidrift validate             # offline PWR candidate verdicts (no WikiWho)
uv run wikidrift prerank              # metadata pre-ranker (offline)
uv run wikidrift profile "Brontosaurus" # descriptive L1 drift profile: recency + editor concentration (offline)
uv run wikidrift analyze "Climate change" # full L1 pipeline (+ WikiWho for confirm/attribute)
uv run wikidrift stance "Abortion"    # L2 stance over time (needs an LLM key)
uv run wikidrift framing "Gaza war"   # L5 Lite matched historical leads (needs an LLM key)
uv run wikidrift framing "Gaza war" --static  # L5 Lite current-lead comparison
uv run wikidrift crosslingual "Zionism"                     # L5 cross-language stance comparison (needs key)
uv run wikidrift factcheck "Warsaw concentration camp" --asof 2018-06-01   # L5 #2 fact divergence (needs key)
uv run wikidrift mscore                                        # controversy corroborator (offline fetch)
uv run wikidrift discover "Nakba"                              # L5→L4 graph-guided discovery (seed → footprint → re-test)
uv run wikidrift sources "Palestine"                           # L5 #3b citation-source change from → to across the pivot
uv run wikidrift ingest "Naliboki massacre"                    # local wikiwho_rs backend → rsnap (then analyze/validate offline)
uv run wikidrift migrate-shards                                # copy + verify canonical data into article-owned shards
```

`migrate-shards` never modifies the canonical corpus. It verifies every copied table and artifact, writes a
manifest per article, and preserves corpus-wide outputs under `articles/_shared/`. Give each parallel worker
its own shard with `WIKIDRIFT_DATA_DIR=.planning/spikes/data/articles/<slug>` before invoking WikiDrift.

Single-article verbs accept either an article title or a Wikipedia URL (for example,
`https://en.wikipedia.org/wiki/Chess`).

`crosslingual` defaults to a topic-specific, auto-selected established-edition set (targeting editions
with stronger article coverage; English kept when present for pivot-relative comparability). Pass
`--langs` to pin an explicit comparison set.

`analyze` persists the exact pair from durable-spine confirmation with the corpus horizon and thresholds
used. `framing` prefers that artifact while it remains current, uses the exact English revisions, and
matches other editions to their timestamps. That result is pivot-relative. A missing or stale artifact
falls back to the top coarse candidate and is labeled candidate-relative; no candidate falls back to
current leads. Pass `--static` to request the last mode directly.

Existing articles need one `analyze` rerun to create the confirmation artifact, followed by `framing` to
refresh only the cross-language lead comparison. The latter does not recompute L1 or the other analysis layers.

For every article already present in the local token corpus, dry-run and then execute the sequential
refresh with:

```bash
uv run python tools/cover_missing_topics.py --all-corpus --mode framing
uv run python tools/cover_missing_topics.py --all-corpus --mode framing --execute
uv run python viewer/build.py
```

Executed framing batches also write one `<slug>.cost.json` per article with stage timings,
provider-reported token usage, and an estimated LLM total when `WIKIDRIFT_LLM_PRICING_JSON` contains a
rate for every model used. Each call record freezes its provider, model, and rates.

`discover` seeds from editors attributed with removals in an article, follows *only their content-removing edits* elsewhere
(a search prior — the graph never flags anything), subtracts the base-rate roster, and re-tests each fresh
candidate by its **own** L1 content; a candidate is a lead only if its own trajectory confirms it (and
born-in-contested pivots are separated from stable-then-retrofit ones). `sources` is reference-agnostic — it
reads the article's own citations (cite-template types + domains, Wayback-unwrapped) and reports the
composition change from → to across the pivot; it **rates no source** (that judgment is contested and would
make the tool dismissible — data-as-is is the stance).

The token corpus (`.planning/spikes/data/provenance.duckdb`, ~350 MB of cached snapshots) is reused as-is.
Offline commands (`benchmark`, `validate`, `prerank`) need only that DB; `analyze`/`stance` call live APIs.

### LLM backend (cost lever, `llm.py`)

The two LLM layers — **L2 stance** and **L5 claim adjudication** — are provider-agnostic. Default mode is
provider auto-selection + failover via `WIKIDRIFT_LLM_PROVIDER_PRIORITY`
(`anthropic,openai,grok,google` unless changed), using whichever provider keys are configured.
Set `WIKIDRIFT_LLM_PROVIDER` (or pass `--provider`) only to pin one provider and disable auto failover.
`claude-sonnet-5` remains the default Anthropic model (near-Opus quality at ~40-60% cost;
`--model claude-opus-4-8` reproduces the certified ★#3 benchmark baseline). A researcher can pick a cheaper
or free/local model:

```bash
# OpenAI (cheap hosted, strict structured output)
wikidrift stance "Photosynthesis" --provider openai --model gpt-4o-mini            # OPENAI_API_KEY
# any OpenAI-compatible endpoint via --base-url: OpenRouter / Together / Groq / DeepSeek / Fireworks …
wikidrift factcheck "Jedwabne pogrom" --provider openai \
    --base-url https://openrouter.ai/api/v1 --model meta-llama/llama-3.3-70b-instruct
# fully local + free (Ollama / LM Studio / vLLM speak the OpenAI API)
wikidrift crosslingual "Hamas" --provider openai --base-url http://localhost:11434/v1 --model llama3.1
# native Google Gemini (very cheap)
wikidrift pipeline "Water" --llm --provider google --model gemini-flash-lite-latest   # GOOGLE_API_KEY
```

Equivalent env vars: `WIKIDRIFT_LLM_PROVIDER` / `_MODEL` / `_BASE_URL` / `_API_KEY` (the last overrides the
provider-native `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` / `XAI_API_KEY` / `GOOGLE_API_KEY`).
If `WIKIDRIFT_LLM_PROVIDER` is unset, auto mode checks configured keys in
`WIKIDRIFT_LLM_PROVIDER_PRIORITY` (default: `anthropic,openai,grok,google`) and fails over to the next
provider on rate/quota exhaustion.
Install the extra SDK only if you
use it: `uv sync --extra openai` / `--extra google` / `--extra all-llm` (the default install needs neither).
Model ids are examples — pick per current availability.

### Default entity focus (no-prior mode)

For standalone and pipeline runs, default entity focus is self-determined from the article itself:

- `wikidrift stance <article>` defaults to entity = article title (unless `--entities` is passed)
- `wikidrift pipeline <article> --llm` forwards L2/L2.5 context into L5 (entities, shifts, lexical JS)
- standalone `crosslingual` also defaults to entity = article title when no explicit context is provided

This keeps the default path controversy-agnostic and avoids hard-coded focal priors.

**Keys via `.env`.** Copy `.env.example` → `.env` (gitignored) and fill in the key for your provider; it's
auto-loaded on import (never overriding an already-set env var), so no manual `source` is needed.

**Free-tier friendly.** Rate-limit (429) + 5xx responses retry with exponential backoff (honoring a
`Retry-After` header when present), so hitting a free-tier limit *pauses and continues* instead of crashing
the run; 4xx client errors surface immediately. For proactive pacing under a tight requests/min cap, set
`WIKIDRIFT_LLM_MIN_INTERVAL` (seconds between calls, e.g. `4` for a 15 RPM tier); tune attempts with
`WIKIDRIFT_LLM_MAX_RETRIES` (default 5).

**Gemini model note.** Prefer a `*-lite` / `*-latest` alias (default `gemini-flash-lite-latest`). On a given
project some models return zero free-tier quota (`gemini-2.0-flash` → `429 limit: 0`) and unversioned aliases
can 404 mid-deprecation (`gemini-2.5-flash`). Validated live on the free tier with `gemini-flash-lite-latest`.

**Local ingestion backend (`ingest`).** The hosted WikiWho API can't serve every article (coverage gaps, e.g.
the Poland-WWII slate) and is the wrong tool for corpus-scale batch. `ingest` populates the *same* `rsnap`
schema from the article's full-history XML via the local `wikiwho` engine, using the Rust `snapshot-tokens`
helper in `tools/snapshot-tokens` (build once: `cd tools/snapshot-tokens && cargo build --release`; override
its path with `WIKIDRIFT_SNAPSHOT_TOKENS`). It depends on the `wikiwho_rs` dump-parser fix (Schuwi/wikiwho_rs
PR #44, from this project). After `ingest`, the offline commands run on the local data.

## Roadmap — not yet built

All validated spikes are now promoted — the last stragglers (002 drift-profile → `drift.profile`, and 007's
base-rate batch → the `bootstrap` verb) landed in Session 08. Known gaps in the package, prioritized:

### Near-term

1. ✅ **Findings-output layer (done, Session 05)** — `crosslingual`/`factcheck`/`mscore` now persist
   viewer-shaped JSON into `config.FINDINGS` (`.planning/spikes/data/findings/`); the viewer merges it over
   the frozen spike `out/` dirs, so a production run on a *new* article flows straight to the site.
2. ✅ **Tests (done, Session 05)** — stdlib `unittest` suite in `tests/` (see below): AA-contrast regression,
   pure-function units, findings-shape checks, and a golden-verdict L1 regression.
3. ✅ **L1→L2→L5 orchestration (done, Session 05)** — `pipeline.py` + `wikidrift pipeline "<article>"` chains
   L1 → pre-rank router → (L2 stance on `addition→L2`/`churn→L2` leads) → L5, offline by default with LLM
   layers opt-in (`--llm`) and the M-score corroborator opt-in (`--mscore`). Closes "adjudicate the routed
   leads": the router's L2 leads are now actually run.

### Built since (Session 08)

- ✅ **L4 graph-guided discovery** (`l4.py`, `wikidrift discover`) — seed → removal footprint → independent
  L1 re-test; the graph is a search prior only. *Not yet wired:* the iterate/snowball step (confirmed hits
  recruit their own removal-attributed editors into the next round) and corpus-scale batch via `ingest`.
- ✅ **L5 #3b citation-source change** (`l5_sources.py`, `wikidrift sources`) — reference-agnostic, no source
  rated.

### Design-deferred

- **L2 section-level segmentation** (stance is whole-article / focal-entity today).
- **Attribution ID→username reconciliation** (WikiWho editor IDs → names).
- **L5 instrument #3 (the *external-reference* form)** — cross-encyclopedia / scholarly-corpus comparison.
  Deliberately not built as a consensus *oracle* (the reference is itself contested on charged topics, and the
  honest temporal version needs revision-provenance other corpora don't publish). Future research; footholds
  = OpenAlex publication-dates, the Wayback Machine, public-domain older editions. See `METHODOLOGY.md`.

## Tests

Stdlib `unittest`, no extra deps. From the repo root:

```bash
python -m unittest discover -s tests           # or: .venv/bin/python -m unittest discover -s tests
```

`test_contrast` (viewer palette stays WCAG AA) · `test_pure` (Jaccard / stance-value / English-gap / M-score
invariants) · `test_findings` (the findings-output layer writes viewer shapes) · `test_golden_verdicts` (L1
regression on the cached corpus — Zionism→PIVOT 824k PWR, Photosynthesis/Nakba/Naliboki→HEALTHY, Water→standing
pivot; auto-skips if the DuckDB corpus is absent) · `test_pipeline` (the orchestrator follows the router —
Nakba routes `addition→L2`).
