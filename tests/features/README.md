# WikiDrift Gherkin Requirements

**Status:** As-built specification with explicit gaps  
**Reviewed:** 2026-08-04  
**Scope:** Public static website and analysis tool  
**Source of truth:** `tests/features/`, linked to the implementation and executable tests

## Purpose

This suite specifies WikiDrift in observable Given/When/Then examples. It separates the public website from the analysis tool. Tool requirements are split by methodology level so a change can be reviewed against the layer that owns it.

The suite describes behavior, not implementation detail. Cucumber automation is optional; the existing Python tests remain the executable regression suite until step definitions are added.

## Files

### Website

- `website.feature` — build, discovery, article pages, evidence tabs, trust states, accessibility, and responsive behavior.

### Tool

- `cross_cutting.feature` — epistemic safety, canonical inputs, artifact contracts, configuration, and failure semantics.
- `l0_corpus.feature` — collection, provenance, integrity, stable endpoints, and article shards.
- `l1_drift.feature` — PWR interval scoring, candidate generation, exact confirmation, profile, and benchmark.
- `l1_attribution.feature` — ordered-revision attribution and editorial-process context.
- `l2_stance.feature` — model-assisted stance trajectories and auditability.
- `l2_additive.feature` — deterministic formative/additive framing trajectories.
- `l2_lexical.feature` — lexical divergence and adequacy.
- `l3_evidence_export.feature` — exact/candidate redline and authorship exports.
- `l4_discovery.feature` — graph-guided candidate discovery and independent confirmation.
- `l5_external_comparisons.feature` — cross-language stance, lead framing, fact divergence, citation composition, and controversy context.
- `pipeline_batch.feature` — orchestration, batch isolation, resume behavior, cost receipts, and CLI outcomes.

## Level Taxonomy

| Level | Contract |
| --- | --- |
| L0 | Establish complete, stable, canonical evidence from public Wikipedia history. |
| L1 | Preserve durable loss, standing-gain, and replacement leads; exactly confirm durable loss from an article's own history. |
| L1.6 | Attribute observable revision actions across confirmed event boundaries. |
| L2 | Audit whether entity-relative stance changed over time. |
| L2a | Track added, removed, retained, and relocated claims deterministically. |
| L2.5 | Describe vocabulary redistribution with explicit comparison adequacy. |
| L3 | Materialize inspectable redlines and authorship spans. |
| L4 | Use confirmed public-account relationships only as a search prior. |
| L5 | Compare external language editions and internal citation composition. |

Supporting instruments such as pre-ranking, M-score, trust resolution, pipeline routing, and batch execution are specified beside the level they inform or in the cross-cutting/pipeline files.

## Tags

- `@implemented` — behavior exists in current code and is generally covered by tests.
- `@policy` — non-negotiable interpretation or publication constraint.
- `@network` — requires public network access unless a valid cache satisfies it.
- `@llm` — requires an explicitly selected/configured LLM provider.
- `@offline` — must not make network or LLM calls.
- `@gap` — accepted requirement not fully implemented or not adequately tested.
- `@website` / `@tool` — product surface.

## Shared Domain Language

- **candidate:** a coarse signal admitted for closer checking; not a finding.
- **confirmed:** exact revision evidence meets the durable-spine confirmation contract.
- **not_confirmed:** exact checking ran and rejected every evaluated candidate.
- **descriptive_anomalies:** one or more sweep anomalies remain visible but cannot receive exact confirmation.
- **healthy:** coarse analysis found no candidate under the active thresholds.
- **unavailable:** evidence was insufficient, stale, incompatible, quarantined, or could not be retrieved.
- **not_applicable:** the layer does not apply to the authoritative upstream state.
- **research lead:** an inspectable observation that requires human adjudication.
- **review priority:** high, review, or low ordering metadata; it never controls anomaly admission or exact checking eligibility.
- **receipt:** persisted evidence sufficient to identify inputs, contract version, and outcome.

`healthy`, `descriptive_anomalies`, `not_confirmed`, `unavailable`, and `not_applicable` are never interchangeable.

## Non-Negotiable Product Rules

1. Every output is a research lead, never a bias, truth, intent, coordination, or misconduct verdict.
2. Exact content evidence overrides coarse candidates downstream.
3. Missing, stale, incompatible, or quarantined evidence fails closed as unavailable; sub-floor measured anomalies remain descriptive rather than becoming unavailable or healthy.
4. Public account names may describe public revision actions. The system never infers real-world identity or merges accounts.
5. Graph and process metadata may prioritize inspection but cannot confirm content change.
6. All summaries remain traceable to revisions, text, citations, or structured receipts.
7. Topic selection and default detector thresholds remain topic-agnostic.

## Review Basis

The project review covered:

- 74 authoritative source/viewer/test files in the structural graph;
- 1,254 graph nodes, 1,981 relationships, and 72 code communities;
- 361 existing unit/integration test examples;
- `README.md`, `PRODUCT.md`, `METHODOLOGY.md`, `src/wikidrift/README.md`, CLI definitions, viewer generation, browser behavior, CI, and four existing vault plans.

Generated `docs/` pages and cached `.planning/` datasets are not independent requirements. Their behavior is specified through the generator, committed finding contracts, and browser-visible output.

## Review Findings and Open Gaps

1. Documentation inconsistently calls `discover`, `sources`, and `mscore` offline. They can require public API retrieval when caches are absent. This suite tags those scenarios `@network` and reserves `@offline` for commands proven not to call the network.
2. L3 is implemented as `viewer/export_l3.py`, not a first-class `wikidrift` CLI verb.
3. Website interaction logic has strong code-level accessibility intent, but tab/hash/mobile/dialog behavior lacks browser automation coverage.
4. The product targets WCAG 2.2 AA intent; the automated contrast gate currently checks WCAG 2.1 AA color ratios only.
5. L2/L5 model outputs preserve receipts and instability evidence, but calibration across neutral controls remains incomplete.
6. Concentration labels remain correctly disabled because current attribution distributions are not discriminating.
7. L4 multi-hop snowball discovery and external non-Wikipedia reference corpora remain deliberately unimplemented.
8. The configured source coverage floor is 38%, below the project's stated preferred core-logic floor; decision modules have stronger tests, but the aggregate gate should be ratcheted over time.

## Change Rule

A behavior change is complete only when its Gherkin example is updated first or in the same change, the corresponding Python/browser regression fails before implementation and passes afterward, and public wording continues to obey the non-negotiable product rules.
