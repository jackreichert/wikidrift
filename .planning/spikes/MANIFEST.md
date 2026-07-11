# Spike Manifest

## Idea

Validate the **editor-agnostic temporal narrative-drift detector** that is the §10 core of the
Wikipedia Filter Mirror project (design: `~/Documents/JackObsidian/encyclopediae/wikipedia-filter-mirror-design.md`;
evidence base: `wikipedia-bias-evidence-findings.md`). Prove end-to-end on a small subset that we can
pull per-token provenance for real articles, compute a drift signal, and separate a contested article
from a stable control — without downloading the 31 TB full-history dump.

## Requirements

Decisions that emerged during spiking. Non-negotiable for the real build unless revisited.

- **Python glue via `uv`**; **DuckDB** for local token facts (matches design §3.3).
- **Provenance = hosted WikiWho API** (`rev_content` with `o_rev_id`/`editor`/`token_id`/`in`/`out`)
  **+ MediaWiki Action API** for the `rev_id → timestamp/user` timeline. English coverage confirmed live.
- **`wikiwho_rs` (Rust, 001b)** is needed for the *pure* smoking-gun signal — it exposes full
  **deleted-token lifecycles**; the hosted API only returns *surviving* tokens.
- **Drift signal must use recency + editor-concentration**, NOT raw in/out churn:
  churn on surviving tokens is **confounded by token age** (older surviving tokens accrue more in/out
  events) and separates *backwards*. Discovered in 002.
- **Never claim manipulation from drift alone.** Drift (instability/recency/concentration) is necessary
  but not sufficient; the §10 conjunction still needs POV-reversal + removed-sourced + persisted-against-
  reverts, which require deleted-token + content analysis.
- **The core structural signal is the deleted-token lifecycle** (001b): "% of long-stable text DELETED
  since a cutoff," compared against a control. It cleanly separates retrofit (delete the old spine) from
  expansion (add around it) — the healthy control preserves >13-yr-stable text (~7% deleted); the
  contested article does not (~76%). Directional (POV) + section-level layers still to come.
- **L5 = external-reference layer** (spike 012), instrument #1 of 3 (cross-lingual → cross-encyclopedia →
  citation-source). Catches born-biased / long-stable bias L1 (change detector) + L2 (temporal, internal)
  are blind to, by comparing an article against EXTERNAL references. Cross-lingual instrument: run the L2
  NPOV classifier on the SAME article across editions (en/he/ar for I-P; en/pl/de for KL Warschau) and diff.
- **L5 must test BOTH static and pivot-relative divergence.** Static (compare editions at "now") is the
  born-biased fallback where there's no pivot. Pivot-relative (cross-lingual divergence *before* vs *after*
  the L1 pivot) is the stronger discriminator: en peeling away from a cross-lingual consensus at the pivot =
  capture; all editions moving together = legitimate real-world event. Reuses L1's detected pivots.
- **Classify native-language text directly** (no translation — Claude is multilingual); NPOV axis, not
  sentiment (Johnson 2025). Output makes disagreement LEGIBLE, never asserts a neutral-truth verdict.
- **Cross-lingual stance instrument scope (012b):** catches **framing** capture (Israel-Palestine — Zionism
  reads neut/symp/crit across en/he/ar), NOT **factual/numerical** distortion (KL Warschau *agrees* on
  stance; its victim-count myth needs L5 instrument #2/#3 — cross-encyclopedia / citation-source). Native
  classification validated (no translation); neutral control agrees (low false-positive). Report **both**
  passage strategies — a fixed *lead* window (fair, style-sensitive) and *focal*-entity sentences
  (whole-body, charged-biased).
- **L5 instrument #2 = cross-edition citation + claim divergence (014), the fact-distortion detector.**
  Claim divergence (LLM extract-then-adjudicate over the same factual questions per edition) catches
  numerical/factual distortion stance is blind to — KL Warschau's ~200k myth surfaces as cross-edition
  contradiction **@2018** and converges **now** (corrected). **Temporal (as-of) is essential**, like #1's
  pivot-relative mode. **Citation Jaccard is confounded** by edition language (low even for the control) →
  use as context, not a flag. Instruments #1 (framing) and #2 (fact) are **complementary** — an article can
  fail either axis alone. L5 instrument #3 (cross-encyclopedia vs Britannica / scholarly corpus) is future.
- **M-score is a corroborator, not a flag (013).** Yasseri mutual-revert M (metadata-only, revert graph)
  MUST be refined (registered editors + sustained ≥2 mutual reverts) or it over-rates vandalism magnets
  ~20×. Even refined it does NOT separate benign from malicious change — Climate (genuinely edit-warred)
  dominates yet its recent rewrite was benign. Use as context: high-M = contested; **low/zero M on a
  flagged article (Nakba, KL Warschau) = a route-to-L5 signal** (smooth consensus / quiet distortion, the
  mode a controversy measure can't judge). Does not, alone, implement directional "persisted-against-reverts."
- **Product direction:** open-source, plug-and-play tool for researchers + a hosted site at
  drift.encyclopediae.org — a GitHub-style side-by-side (PR-diff) viewer of precompiled compared articles,
  a request form, methodology, and "receipts" (provenance/evidence). Viewer also shows the article's
  **pivot-history timeline** (from L1) with each major pivot clickable to its diff, and **"blame"**
  (GitHub-style per-span/token authorship from WikiWho provenance + drift.py attribution — the "named-list
  overlay" already in the spike-003 design). So every spike emits structured artifacts (per-edition prose +
  stance + evidence + provenance) — those ARE the receipts and the viewer's data source. The viewer is L3
  (spike 003) evolved cross-edition. All three viewer features surface data we already compute.

## Spikes

| # | Name | Type | Validates | Verdict | Tags |
|---|------|------|-----------|---------|------|
| 001a | provenance-wikiwho-api | comparison | Per-token `{editor, origin_rev, origin_time, in/out}` for a real enwiki article via hosted WikiWho API + Action API timeline | ✅ VALIDATED | provenance, wikiwho, api, duckdb |
| 001b | provenance-wikiwho-rs | comparison | Local Rust engine + **deleted-token lifecycle**: long-stable text DELETED (retrofit) vs diluted (expansion) | ✅ VALIDATED | provenance, wikiwho, rust, dumps, deleted-tokens |
| 002 | drift-signal-separation | standard | Drift profile materially separates a contested article (Zionism) from a stable control (Photosynthesis) | ✅ VALIDATED (w/ caveat) | drift, signal, thesis |
| 004 | pivot-detection | standard | Discover WHEN an article pivoted (change-point) + binary-search the exact revision; classify PIVOT/CREEP/HEALTHY | ✅ VALIDATED | pivot, change-point, binary-search |
| 005 | analyzer | standard | Robust article-agnostic analyzer: persistent-revision snapshots, multi-pivot magnitude-ranked + binary-search-confirmed, + attribution (destroyers/replacers) | ✅ VALIDATED | analyzer, robust, attribution |
| 006 | entity-stance | standard | Framing/entity-stance over time (addition-side + semantic confirmation); framing-lexicon works, VADER weak | ✅ VALIDATED | l2, stance, framing |
| 007 | base-rate | standard | Designed control slate → "contested churns": the drift signal is a *change* detector, not a *bias* detector | ✅ VALIDATED | base-rate, calibration |
| 008 | prerank | standard | Metadata-only candidate pre-ranker; routes `removal→PWR` / `addition→L2` / `churn→L2` leads (no text, no WikiWho) | ✅ VALIDATED | prerank, metadata, scaling |
| 009 | benchmark | standard | Adjudicated ground-truth benchmark; **certified** on real must-flag data (removal 5/5, reframe 3/6, controls 3/3) | ✅ CERTIFIED | benchmark, ground-truth, ★#3 |
| 010 | l2-stance | standard | Production LLM stance classifier on an NPOV axis (not sentiment); discriminates on history, blind to born-framed | ✅ VALIDATED | l2, stance, llm |
| 011 | local-ingest | standard | Local `wikiwho_rs` ingestion (`snapshot-tokens` Rust helper + Action-API→XML assembler → rsnap); **fixed a dump-parser entity bug** (patch preserved) | ✅ VALIDATED | wikiwho_rs, dumps, local, fix |
| 012a | crosslingual-align | standard | Resolve the SAME article across editions via Wikidata sitelinks + fetch current/as-of prose (en/he/ar and en/pl/de) | ✅ VALIDATED | l5, crosslingual, wikidata, alignment |
| 012b | native-stance | standard | Classify non-English prose (he/ar/pl/de) with the L2 NPOV classifier natively (no translation); focal-entity via Wikidata labels vs. lead-section (compare) | ✅ VALIDATED (w/ caveats) | l5, crosslingual, stance, llm |
| 012c | divergence-signal | standard | Diff per-edition stances: born-biased articles diverge, neutral control agrees; test static AND pivot-relative (before/after L1 pivot) divergence | ✅ VALIDATED | l5, crosslingual, divergence, thesis |
| 013 | mscore | standard | Yasseri mutual-revert M-score (prior-art #3) — metadata-only controversy corroborator; refined M needed (vandalism confound); does NOT solve base-rate | ✅ VALIDATED (w/ caveats) | mscore, revert, controversy, metadata, prerank |
| 014 | citation-claim-divergence | standard | **L5 instrument #2** — cross-edition citation + claim divergence (as-of aware); catches *factual* distortion (KL Warschau) that stance misses | ✅ VALIDATED (w/ caveat) | l5, crosslingual, citation, claim, fact-distortion |
| 003 | highlight-overlay | standard | **L3 — realized in the viewer:** before/after-pivot side-by-side diff + WhoColor "blame" (lead), from `viewer/export_l3.py` → static site | ✅ VALIDATED (viewer L3) | ui, render, static, l3, blame, diff |

**Promotion (Session 03):** the live engine (001a/b, 005, 008, 009, 010) is consolidated into `src/wikidrift/`
(config · provenance · drift · prerank · stance · benchmark · cli) with a `wikidrift` CLI. These spikes stay
as the frozen experimental record. See `../../src/wikidrift/README.md` and the vault design/methodology docs.

**Promotion (Session 04):** L5 + M-score promoted — `l5_crosslingual.py` (012a/b/c, framing: static +
pivot-relative), `l5_factcheck.py` (014, fact/citation divergence, as-of aware), `mscore.py` (013, refined M);
`config.action(lang)`/`WIKIDATA` added; CLI verbs `crosslingual` / `factcheck` / `mscore` wired.

**Promotion (Session 05):** the local `wikiwho_rs`-on-dumps ingestion backend (011) promoted to
`src/wikidrift/ingest.py` + CLI verb `ingest`; the `snapshot-tokens` Rust helper moved out of the spike to
`tools/snapshot-tokens/`, now depending on **upstream `Schuwi/wikiwho_rs` `main`** (wikiwho v0.3.5, PR #44
merged; Cargo.lock pins the commit) — decoupled from the spike checkout.
`provenance.snapshot_picks` extracted so the hosted and local backends select identical snapshots;
`config.SNAPSHOT_TOKENS_BIN`/`XML_CACHE` added. Validated end-to-end on *Naliboki massacre* (656 revisions →
39 snapshots → 92,403 rsnap rows → offline L1 verdict). This is the corpus-scale / coverage-gap substrate.
