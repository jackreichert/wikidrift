# WikiDrift Requirements Traceability

**Status:** Initial as-built map  
**Reviewed:** 2026-08-04

This map identifies the owning implementation and strongest existing validation areas for each feature file. It is not a claim that every Gherkin scenario already has a one-to-one automated step definition.

| Requirement file | Primary implementation | Existing validation | Remaining gap |
| --- | --- | --- | --- |
| `website.feature` | `viewer/build.py`, `viewer/site.js`, `viewer/index.js`, `viewer/style.css`, `viewer/check_contrast.py` | `tests/test_build.py`, `tests/test_findings.py`, `tests/test_contrast.py` | Browser-level keyboard, hash, dialog, responsive, search/filter/sort/pagination tests. |
| `cross_cutting.feature` | `src/wikidrift/config.py`, `corpus.py`, `pipeline.py`, CLI/config/storage helpers | `tests/test_config.py`, `tests/test_pipeline.py`, `tests/test_pure.py` | One verified command/network classification and stable JSON error schemas. |
| `l0_corpus.feature` | collection, client, corpus, integrity, shard, and canonical-title modules under `src/wikidrift/` | `tests/test_engine.py`, `tests/test_pipeline.py`, `tests/test_pure.py` | More fixture-backed pagination/interruption and shard-conflict integration tests. |
| `l1_drift.feature` | `src/wikidrift/drift.py`, threshold config, `pipeline.py`, benchmark helpers | `tests/test_engine.py`, `tests/test_golden_verdicts.py`, `tests/test_pure.py`, `tests/test_pipeline.py`, `tests/test_build.py` | Calibrate standing-gain and paired-change floors against adjudicated additions, semantic replacements, reverts, splits or merges, and neutral controls. |
| `l1_attribution.feature` | attribution and editorial-process helpers under `src/wikidrift/`; viewer receipt rendering | `tests/test_pure.py`, `tests/test_build.py`, `tests/test_export_l3.py` | Calibrated concentration controls; qualitative labels remain disabled. |
| `l2_stance.feature` | stance/LLM modules under `src/wikidrift/` and pipeline routing | `tests/test_llm.py`, `tests/test_pipeline.py`, `tests/test_pure.py` | Neutral controls, repeated-run calibration, stable section alignment. |
| `l2_additive.feature` | additive/formative trajectory modules under `src/wikidrift/` | `tests/test_pure.py`, `tests/test_pipeline.py` | Expand explicit ambiguous-match and relocation fixtures as the contract evolves. |
| `l2_lexical.feature` | lexical analysis and snapshot routing under `src/wikidrift/` | `tests/test_pure.py`, `tests/test_pipeline.py`, `tests/test_build.py` | More adequacy and tokenizer-contract regression fixtures. |
| `l3_evidence_export.feature` | `viewer/export_l3.py`, viewer templates/styles | `tests/test_export_l3.py`, `tests/test_build.py` | First-class CLI surface and browser rendering tests. |
| `l4_discovery.feature` | graph/discovery modules under `src/wikidrift/`, pipeline/CLI routing | `tests/test_engine.py`, `tests/test_pipeline.py`, `tests/test_pure.py` | Multi-hop expansion intentionally absent; more API fixture coverage. |
| `l5_external_comparisons.feature` | cross-language, fact, source, lead, framing, M-score modules under `src/wikidrift/` | `tests/test_contrast.py`, `tests/test_llm.py`, `tests/test_pipeline.py`, `tests/test_pure.py`, `tests/test_build.py` | External non-Wikipedia corpus, stronger model calibration, language-availability fixtures. |
| `pipeline_batch.feature` | CLI, `pipeline.py`, batch/backfill/shard/cost modules, `.github/workflows/ci.yml` | `tests/test_pipeline.py`, `tests/test_config.py`, `tests/test_llm.py`, `tests/test_pure.py`, full unittest discovery | Deterministic replacement for two live-network tests; higher aggregate coverage floor. |

## Critical Decision Paths

These paths require negative and unavailable-state tests in addition to success tests:

1. Canonical title and page identity resolution before shard selection.
2. Corpus completeness and quarantine before analysis.
3. L1 freshness before any event-relative downstream stage.
4. Exact confirmation overriding coarse candidate evidence.
5. L4 independent retest with graph features excluded from the decision.
6. L5 retrieval/model failure remaining unavailable rather than agreement.
7. Publication trust withholding stale or incompatible artifacts.
8. Website missing-data states preserving the expected evidence structure.
9. Sweep anomalies remaining visible when exact confirmation is unavailable or inapplicable.

## Automation Priorities

1. Add browser acceptance tests for tabs/hash history, mobile navigation, dialogs, findings controls, keyboard behavior, and responsive overflow.
2. Replace deterministic CI dependence on live Wikipedia with recorded/fake API fixtures while retaining optional live smoke tests.
3. Add a lightweight Gherkin syntax/duplicate-title check to documentation validation.
4. Ratchet source coverage above 38% without weakening current gates.
5. Add calibration corpora before enabling stance confidence or attribution-concentration labels.

## Deliberate Non-Goals

- De-anonymization or real-world identity resolution.
- Bias, intent, truth, policy-compliance, or misconduct classification.
- Source reliability ratings.
- Graph-only confirmation.
- Automatic L4 snowball expansion.
- Treating another Wikipedia edition as canonical truth.
- Replacing human adjudication with a composite score.
