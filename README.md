# WikiDrift

**An editor-agnostic, temporal narrative-drift detector for Wikipedia.**

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
|---|---|---|
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
uv run wikidrift bootstrap "Zionism" "Palestine" "Photosynthesis"

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
uv run wikidrift profile "Zionism"             # descriptive L1 profile: recency + editor concentration (offline)
uv run wikidrift analyze "Zionism"             # full L1: drift → pivots → binary-search confirm → attribution
uv run wikidrift discover "Zionism"            # L4: seed → destructive footprint → independent L1 re-test
uv run wikidrift sources "Palestine"           # L5 #3b: citation-source change (from → to across the pivot)
uv run wikidrift stance "Nakba"                # L2 framing/stance over time            (needs an LLM key)
uv run wikidrift crosslingual "Zionism"        # L5 #1: cross-lingual framing divergence (needs an LLM key)
uv run wikidrift factcheck "Warsaw concentration camp" --asof 2018-06-01   # L5 #2: fact divergence (LLM key)
uv run wikidrift mscore                         # controversy corroborator (metadata only)
uv run wikidrift pipeline "Nakba" --llm         # L1 → router → (L2/L5) orchestration for one article
```

Full per-verb detail, the module map, and the LLM-backend options are in
**[`src/wikidrift/README.md`](src/wikidrift/README.md)**.

## LLM keys (L2 / L5 only)

The engine (L1/L4) and `sources`/`mscore` need no key. The framing (L2, L5 #1) and fact (L5 #2) layers call
an LLM. Copy `.env.example` → `.env` and set the key for your provider (auto-loaded, gitignored):

```bash
cp .env.example .env      # then fill in ANTHROPIC_API_KEY (default) or OPENAI_API_KEY / GOOGLE_API_KEY
```

Default backend is Anthropic `claude-sonnet-5`; any OpenAI-compatible endpoint (OpenRouter/Groq/local
Ollama/vLLM) or Google Gemini is selectable via `--provider/--model/--base-url` or `WIKIDRIFT_LLM_*` env.
Optional SDKs: `uv sync --extra openai` / `--extra google` / `--extra all-llm`. Details in the tool README.

## Build the viewer (the site)

The static site under `docs/` is a **compilation of findings**, not the tool. Rebuild it from the committed
findings (no corpus, no keys needed):

```bash
uv run python viewer/build.py          # regenerates docs/ from findings JSON
uv run python viewer/check_contrast.py # verify the palette stays WCAG 2.1 AA
open docs/index.html                    # preview
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

```
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
