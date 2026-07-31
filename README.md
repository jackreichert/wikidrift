# WikiDrift

**An editor-agnostic, temporal narrative-drift detector for Wikipedia.**

**[Live site → wikidrift.encyclopediae.org](https://wikidrift.encyclopediae.org/)**

WikiDrift reads a Wikipedia article against *its own edit history* and against *its other-language editions*
to surface where a long-stable narrative was rewritten, when, by which edits — and whether the result diverges
from how independent editions frame or state the same thing. It is built to **make disagreement legible**, not
to assert a verdict.

Two disciplines are load-bearing and run through everything here:

- **Content-first, never list-first.** No article is flagged because a given editor touched it — only by its
  own content trajectory. Any named list is an optional, sourced, swappable *overlay*, never the foundation.
- **Every output is a lead, never a verdict.** WikiDrift is a *change* detector, not a *bias* detector (a big
  rewrite alone proves nothing — the base-rate finding). Separating benign change from capture is the job of
  the upper layers, and even then the answer is a candidate for a human to adjudicate.

See [`METHODOLOGY.md`](METHODOLOGY.md) for the full design, the layer model, and the epistemic guardrails —
read it before drawing any conclusion from a finding.

---

## Quick start

Prerequisites: **Python ≥ 3.11** and [`uv`](https://docs.astral.sh/uv/). (Optional: a Rust toolchain, only
for the local `wikiwho_rs` ingestion backend; an LLM API key, only for the L2/L5 layers.)

```bash
git clone <repo> && cd wikidrift
uv sync                              # create the env + install the `wikidrift` CLI
uv run python -m unittest discover -s tests    # sanity check (DB-dependent tests auto-skip)
```

`uv sync` installs the package and its `wikidrift` entry point. Everything below is `uv run wikidrift <verb>`.

## What you can run immediately vs. what needs data

There are **two independent artifacts** in this repo, and only one needs the (large, untracked) token corpus:

| You want to… | Needs the token corpus (`provenance.duckdb`)? | Notes |
| --- | --- | --- |
| **Build / view the site** (`viewer/`) | **No** | Reproduces from the committed findings JSON. See "Build the viewer" below. |
| **Re-run the analysis engine** (`analyze`, `validate`, `benchmark`, `discover`, `sources`…) | **Yes** | The corpus is gitignored (binary, ~350 MB–1 GB, regenerable). You bootstrap it — see below. |

The findings JSON (`.planning/spikes/data/findings/`) and the built site (`docs/`) **are committed**, so a
fresh clone can build and inspect the viewer with zero setup.

## Bootstrap the token corpus (for the engine)

The corpus (`.planning/spikes/data/provenance.duckdb`) is **not** in git — it's a regenerable cache of
per-revision token snapshots fetched from public APIs. Build it by analyzing the articles you care about; each
`analyze` fetches from WikiWho + the MediaWiki Action API and caches into the DB. After that, the **offline**
verbs (`validate`, `benchmark`, `prerank`, `discover`, `sources`, `verdict_dict`) run against the cache with no
network.

```bash
# Populate the whole adjudicated roster in one command (sequential, hosted WikiWho, polite — takes a while):
uv run wikidrift bootstrap

# …or just a specific slate:
uv run wikidrift bootstrap "Photosynthesis" "Chess" "Water"

# One article the full way (fetch → confirm → attribute):
uv run wikidrift analyze "Zionism"

# Coverage gaps the hosted API can't serve (quieter articles) → local wikiwho_rs backend (needs the Rust helper):
uv run wikidrift ingest "Naliboki massacre"
```

> `bootstrap` reports each article's verdict as it populates, and flags hosted-coverage gaps (which need the
> local `ingest` backend). The adjudicated roster lives in `src/wikidrift/benchmark.py` (`ROSTER`).

## Command cheatsheet

```bash
uv run wikidrift bootstrap                      # populate the token corpus for the roster (fetch, sequential)
uv run wikidrift benchmark                     # score the adjudicated roster (offline)
uv run wikidrift validate                      # offline PWR candidate verdicts (no WikiWho)
uv run wikidrift profile "Brontosaurus"        # descriptive L1 profile: recency + editor concentration (offline)
uv run wikidrift analyze "Climate change"      # full L1: interval/rolling candidates → confirm → attribution
uv run wikidrift confirmed-graph .planning/spikes/data/articles  # L4: fresh exact cross-shard graph (offline)
uv run wikidrift discover "Nakba"              # L4: exact seed → footprint → independent exact L1 confirmation
uv run wikidrift sources "Palestine"           # L5 #3b: citation-source change (from → to across the pivot)
uv run wikidrift stance "Abortion"             # L2 framing/stance over time            (needs an LLM key)
uv run wikidrift framing "Gaza war"            # L5 Lite: prefers fresh confirmed L1 pair (LLM key)
uv run wikidrift framing "Gaza war" --static   # L5 Lite: compare current leads only
uv run wikidrift crosslingual "Anti-Zionism"   # L5 cross-language stance comparison (needs an LLM key)
uv run wikidrift factcheck "Warsaw concentration camp" --asof 2018-06-01   # L5 #2: fact divergence (LLM key)
uv run wikidrift mscore                         # controversy corroborator (metadata only)
uv run wikidrift pipeline "Hamas" --llm --framing  # L1 → router → L2 + cross-language lead comparison
uv run wikidrift migrate-shards                # lossless canonical corpus → article-owned DuckDB shards
uv run python tools/cover_missing_topics.py --all-shards --mode attribution --execute --jobs 3 --no-resume
                                                # offline schema/attribution backfill for fresh confirmed shards
```

`migrate-shards` leaves the canonical corpus untouched, verifies per-table row counts and artifact checksums,
and stores corpus-wide outputs under `articles/_shared/`. To run independent writers in parallel, point each
process at a different shard before startup:

```bash
WIKIDRIFT_DATA_DIR=.planning/spikes/data/articles/Ilhan_Omar \
  uv run wikidrift analyze "Ilhan Omar"
```

The historical canonical location remains the default when `WIKIDRIFT_DATA_DIR` is unset.

Single-article verbs (`analyze`, `stance`, `framing`, `crosslingual`, `factcheck`, `pipeline`, `discover`, `sources`,
`lexical`, `profile`) accept either an article title or a Wikipedia URL (e.g.
`https://en.wikipedia.org/wiki/Jedwabne_pogrom`).

Default entity focus for L2/L5 is now **self-determined and controversy-agnostic**:

- unless you pass `--entities`, L2 uses the article title as its entity target,
- pipeline and standalone L5 consume that same default (or L2 output when available),
- no hard-coded controversy focal fallback is used in the default path.

For L5 cross-lingual, pivot-relative comparison currently uses one shared L1 pivot boundary for all
selected editions in a run (not separate per-language pivot dates).

Cross-lingual edition defaults are now auto-selected per topic from established language editions with
available prose depth (English kept when available for pivot-relative comparability). Pass `--langs` to
pin an explicit set.

### Refresh cross-language lead findings

Existing framing JSON does not gain historical evidence automatically after a code update. Refresh only
the cross-language lead result for each published article that needs matched revisions and oldid receipts:

```bash
uv run wikidrift analyze "Gaza war"       # one-time: persist exact confirmed pair
uv run wikidrift framing "Gaza war"
uv run wikidrift analyze "Zionism"
uv run wikidrift framing "Zionism"
uv run python viewer/build.py
```

`analyze` writes the exact confirmed revision pair plus the corpus horizon and thresholds used. `framing`
trusts that artifact only while those values still match, then fetches exact English revisions and
timestamp-matched revisions from the other editions. The framing command itself does **not** rerun L1,
lexical analysis, source analysis, or the other L5 instruments. It does call public Wikipedia APIs and
the configured LLM.

If a fresh confirmation is unavailable, `framing` falls back to the coarse cached candidate and labels
the result candidate-relative. If L1 has no candidate, it compares current leads in static mode. Use
`--static` to choose that mode explicitly.

To refresh confirmation and the cross-language lead comparison for every article already downloaded into the local DuckDB,
first inspect the queue, then execute it:

```bash
uv run python tools/cover_missing_topics.py --all-corpus --mode framing
uv run python tools/cover_missing_topics.py --all-corpus --mode framing --execute
uv run python viewer/build.py
```

The first command is a dry run. The executing command processes articles sequentially, running full L1
confirmation before the lead comparison for each one. It needs the existing token corpus, network access for
revision retrieval, and a configured LLM key.

Each executed article writes `<slug>.cost.json` beside its other findings. The report records elapsed
time for `analyze` and `framing`, provider-reported input/output tokens for every successful LLM call,
the provider and model that served each call, and an optional USD estimate. Configure estimates with
rates from your actual provider account, expressed in USD per million tokens:

```dotenv
WIKIDRIFT_LLM_PRICING_JSON={"anthropic:claude-sonnet-5":{"input_per_million":YOUR_RATE,"output_per_million":YOUR_RATE}}
```

The report freezes the rates used for each call. Its dollar total covers LLM token charges only; local
compute, storage, payment fees, taxes, service margin, and any charge not exposed in a successful
provider response remain outside the estimate. Wikipedia and WikiWho public APIs are not assigned a
made-up price.

When the corpus lives on another computer, push this code branch from the development computer and pull
it on the corpus computer. Do not add `provenance.duckdb` to Git; it is intentionally ignored and should
stay on that machine. Run the commands above there, then commit and push the tracked findings and rebuilt
`docs/` pages. Pull that result back on the development computer.

### Batch-fill missing or partial topics

Use the helper script to pass an explicit topic list and choose either:

- `--mode full` (always run pipeline + sources + profile), or
- `--mode fill` (run only what is missing).

Current helper defaults for pipeline invocations:

- LLM path is enabled by default (use `--no-llm` to disable),
- M-score is enabled by default (use `--no-mscore` to disable),
- The cross-language lead comparison is opt-in: add `--framing` to the batch pipeline,
- L5 factcheck language cap defaults to `--l5-max-langs 6` with `--l5-cap-policy adaptive`
  (per-topic auto-tuning from latest factcheck diagnostics),
- set `--l5-cap-policy fixed` to always use the configured cap,
- set `--l5-max-langs 0` for no cap.

```bash
# preview only, explicit list
uv run python tools/cover_missing_topics.py --topics "Ainu people" "Genocide of indigenous peoples" --mode full

# execute explicit list, full workflow
uv run python tools/cover_missing_topics.py --topics "Bar Kokhba Revolt" "UNRWA" --mode full --execute

# analyze then run the pipeline in four article-isolated workers
uv run python tools/cover_missing_topics.py --topics "Capitalism" "Socialism" --mode pipeline --jobs 4 --execute

# execute explicit list, fill only missing layers
uv run python tools/cover_missing_topics.py --topics "History of Zionism" "Gaza war" --mode fill --execute

# still available: run for controls or all discovered partial topics
uv run python tools/cover_missing_topics.py --only-controls --mode fill --execute
uv run python tools/cover_missing_topics.py --mode fill --execute

# run the full discovered topic list with LLM layers
uv run python tools/cover_missing_topics.py --mode full --execute

# opt-out examples (faster/lighter)
uv run python tools/cover_missing_topics.py --mode full --execute --no-mscore
uv run python tools/cover_missing_topics.py --mode full --execute --no-llm
uv run python tools/cover_missing_topics.py --mode full --execute --l5-max-langs 0
uv run python tools/cover_missing_topics.py --mode full --execute --l5-cap-policy fixed --l5-max-langs 6
```

Parallel runs stream each child line with a topic prefix such as `[Capitalism]` while retaining the
same output in `.planning/spikes/data/articles/<slug>/logs/coverage.log`. Successful stages are recorded
in each article's `coverage-state.json` and skipped on resumed runs unless `--no-resume` is passed.

Full per-verb detail, the module map, and the LLM-backend options are in
**[`src/wikidrift/README.md`](src/wikidrift/README.md)**.

## LLM keys (L2 / L5 only)

The engine (L1/L4) and `sources`/`mscore` need no key. The framing (L2, L5 #1) and fact (L5 #2) layers call
an LLM. Copy `.env.example` → `.env` and set the key for your provider (auto-loaded, gitignored):

```bash
cp .env.example .env      # then fill in ANTHROPIC_API_KEY (default) or OPENAI_API_KEY / GOOGLE_API_KEY
```

Default mode is provider auto-selection + failover. If `WIKIDRIFT_LLM_PROVIDER` is not set, WikiDrift checks
configured keys and follows `WIKIDRIFT_LLM_PROVIDER_PRIORITY` (default:
`anthropic,openai,grok,google`), failing over to the next provider on rate/quota exhaustion.
OpenAI-compatible endpoints (OpenAI/OpenRouter/Groq/local Ollama/vLLM), xAI Grok, and Google Gemini are
all supported via `--provider/--model/--base-url` or `WIKIDRIFT_LLM_*` env.
Set `WIKIDRIFT_LLM_PROVIDER` (or pass `--provider`) only when you want to pin a single provider and disable
auto failover.
Optional SDKs: `uv sync --extra openai` / `--extra google` / `--extra all-llm`. Details in the tool README.

## Build the viewer (the site)

The static site under `docs/` is a **compilation of findings**, not the tool. Rebuild it from the committed
findings (no corpus, no keys needed):

```bash
uv run python viewer/build.py          # regenerates docs/ from findings JSON
uv run python viewer/check_contrast.py # verify the palette stays WCAG 2.1 AA
open docs/index.html                    # preview
```

On a machine with the token corpus, refresh the current L1 rewrite status and exact-revision diff artifacts
before building. The exporter automatically discovers every published article from the committed profile
findings; there is no article roster to maintain:

```bash
uv run python viewer/export_l3.py       # requires provenance.duckdb + public Wikipedia APIs
uv run python viewer/build.py
```

Optional: auto-categorize topic filters with an LLM during site build (cached for repeat runs):

```bash
uv run python viewer/build.py --llm-categories
# cache file (default): .planning/spikes/data/findings/topic_categories.json
```

To recompute all cached categories:

```bash
uv run python viewer/build.py --llm-categories --refresh-categories
```

## Tests

```bash
uv run python -m unittest discover -s tests
```

Stdlib `unittest`, no extra deps. Pure-function units, findings-shape checks, viewer AA-contrast regression,
a synthetic-corpus L1 engine test (runs in CI without the corpus), and a golden-verdict L1 regression
(auto-skips if the token corpus is absent). See the tool README for what each suite covers.

Coverage is enforced in CI via `coverage` with a regression floor (`fail_under` in `pyproject.toml`):

```bash
uv run --with coverage python -m coverage run -m unittest discover -s tests && uv run --with coverage python -m coverage report
```

## Repo map

```text
src/wikidrift/     the tool (engine + CLI)  — see src/wikidrift/README.md
viewer/            static-site generator (build.py) + templates/style
docs/              the built site (committed; GitHub Pages)
tests/             stdlib unittest suite
tools/             Rust snapshot-tokens helper for the local wikiwho_rs backend
.planning/spikes/  frozen experimental record + the (gitignored) data cache + committed findings/
.github/workflows/ CI — tests (py3.11/3.12) + viewer build + WCAG AA on every push/PR
METHODOLOGY.md     the design, the layer model, and the epistemic discipline — read this
```

CI (`.github/workflows/ci.yml`) runs the test suite and the site build + contrast check on every push and
PR. It needs no secrets, no token corpus, and no LLM keys — the DB-dependent test auto-skips and the site
builds from committed findings. Pages **deployment** is intentionally not wired (it stays a manual step).

## Status & scope

WikiDrift is a research tool. It surfaces **candidates for a human to adjudicate**; it names *actions* from
public data, never intent; it does not assert "the neutral truth." What it does *not* do, and the limits it is
honest about (born-biased blind spot, base-rate, external-reference asymmetry), are in `METHODOLOGY.md`.

## License

The **code** is licensed under **Apache-2.0** (see [`LICENSE`](LICENSE)). **Displayed Wikipedia text**
(article prose, diffs, quotes) is authored by Wikipedia contributors and remains under **CC BY-SA 4.0** — it is
*not* covered by the code license; reusing it requires attribution + share-alike. WikiDrift is an independent
project, **not affiliated with or endorsed by the Wikimedia Foundation**. See [`NOTICE`](NOTICE) for details.
