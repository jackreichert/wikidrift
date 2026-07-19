# Transition Plan: No-Prior L5

Current defaults are controversy-agnostic for entity focus in L2/L5. Two remaining curated priors in L5 are
still explicit by design: `crosslingual` language slate defaults and `factcheck` per-article question sets.
This checklist tracks the transition from curated defaults to generated defaults.

**Status:** Phase 1 is partially implemented. Cross-language checks now discover available editions and
rank additional languages by article size, but configured topic slates are still pinned. Phases 2–4 have
not been implemented and remain the active roadmap.

## Operator checklist (what you do)

Now:

1. Set provider keys in `.env` for providers you want available.
2. Set `WIKIDRIFT_LLM_PROVIDER_PRIORITY` to your desired order (cost/quality preference).
3. Run `pipeline --llm` normally; provider failover is automatic in default mode.
4. Review findings as leads and adjudicate with domain judgment.

Later (during this transition):

1. Compare curated vs generated runs once generated mode exists.
2. Validate benchmark + controls against the acceptance criteria below.
3. Approve default switch only after quality thresholds pass.

## Phase 1: Crosslingual language auto-selection

- [x] Derive candidate editions from Wikidata sitelinks for each article.
- [x] Keep `en` and fill available slots with established editions ranked by prose/article length.
- [ ] Remove article-specific `SLATE` pins after calibration shows the automatic set is sufficient.
- [ ] Add minimum-prose and availability thresholds; return `insufficient` when thresholds fail.

Current behavior:

- `crosslingual` pins any configured `SLATE` editions, then adds two established editions ranked by prose
  length and retains English as the anchor.
- Framing Lite pins a category slate, adds up to two non-English editions ranked by article byte length,
  excludes articles below 2,000 bytes, and caps the candidate set at five including English.
- Temporal framing drops an edition if either matched historical lead is unavailable. It does not yet
  backfill the next-ranked language.

Acceptance criteria:

1. Default `crosslingual` run requires no article-specific language slate.
2. Selected language set is persisted in findings and is reproducible on rerun.
3. Controls do not show inflated divergence solely from low-content editions.

## Phase 2: Factcheck question auto-generation

- [ ] Generate load-bearing factual questions from article lead + infobox with a constrained schema.
- [ ] Run a first-pass extraction and drop low-coverage questions across editions.
- [ ] Keep adjudication deterministic (`agree` / `differ` / `contradict` / `insufficient`) on normalized values.

Acceptance criteria:

1. Default `factcheck` run requires no article-specific `QUESTIONS` entry.
2. Generated questions and retained questions are persisted in findings.
3. Contradiction rate on controls remains within calibration tolerance.

## Phase 3: Reproducibility and safety guardrails

- [ ] Cache generated language sets and question sets by `(article, asof)`.
- [ ] Add explicit neutral fallback template (dates, counts, actors) when generation quality is poor.
- [ ] Add a mode flag to run curated and generated paths side-by-side for calibration.

Acceptance criteria:

1. Same input state yields identical selected languages/questions.
2. Fallback path is explicit in output metadata.
3. Generated and curated modes can be compared in one run.

## Phase 4: Default switch

- [ ] Run calibration on benchmark + controls with curated and generated modes.
- [ ] Document drift deltas and failure cases.
- [ ] Flip generated mode to default only after criteria pass.

Acceptance criteria:

1. Generated mode meets agreed quality thresholds on benchmark and controls.
2. Curated mode remains available as a fallback while stabilization completes.
3. Methodology and site docs reflect the new default behavior.
