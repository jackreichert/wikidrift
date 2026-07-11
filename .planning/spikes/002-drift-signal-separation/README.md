---
spike: 002
name: drift-signal-separation
type: standard
validates: "Given per-token provenance for a contested article vs a stable control, when the drift profile runs, then the contested article separates materially on recency + editor-concentration"
verdict: VALIDATED
related: [001a, 001b]
tags: [drift, signal, thesis]
---

# Spike 002: Drift-signal separation (the thesis test)

## What This Validates
Given per-token provenance (from 001a) for a **contested** article (Zionism) and a **stable control**
(Photosynthesis), when the §10 drift profile runs, then the contested article separates materially —
proving the editor-agnostic drift signal carries information, before any named list is applied.

## How to Run
```bash
uv run python drift_profile.py "Zionism" "Photosynthesis"   # reads ../data/provenance.duckdb
```

## Results — VALIDATED (with one sharp caveat)

Both articles are the same age (first token ~Oct/Nov 2001). The drift profile:

| Metric | Zionism (contested) | Photosynthesis (control) | Separation |
|---|---|---|---|
| surviving tokens | 88,667 | 27,158 | — |
| **median age of current text** | **1.41 yrs** | **9.11 yrs** | contested far younger |
| **% current text authored POST-Oct-7-2023** | **88.95%** | **21.85%** | **4.1× (contested)** |
| % current text pre-2019 (long-stable) | 7.63% | 58.19% | contested retains almost none |
| tokens per editor (concentration) | 220.6 | 63.5 | 3.5× (contested) |
| **top-10 editors' share of current text** | **74.3%** | **42.8%** | 1.7× (contested) |
| % tokens ever churned (in/out) | 28.4% | 33.4% | **does NOT separate** |
| mean churn intensity (in+out) | 1.71 | 8.28 | **runs backwards** |

**The signal separates dramatically on recency + concentration.** A 25-year-old article (Zionism)
whose *current* text is ~89% authored after Oct 7 2023, held by a top-10 editor cohort owning ~74%
of it, is a fundamentally different object from a same-age article that retains text evenly from
every era (Photosynthesis). Median current-text age of **1.4 vs 9.1 years** is the headline.

### The caveat (depth-over-speed finding)
**Raw in/out churn on *surviving* tokens does not separate the two — it runs backwards** (control
higher). Investigated and confirmed the cause: **churn is confounded by token age.** Mean churn by
origin era:

```
                     Zionism        Photosynthesis
   pre-2010      n=1446  14.52      n=7377  30.01     <- ancient surviving tokens: huge churn
   2010-2018     n=5323   1.86      n=8426   0.30
   2019-Oct2023  n=3025   1.32      n=5421   0.10
   post-Oct7     n=78873  1.48      n=5934   0.08
```

Old surviving tokens accrue in/out events simply by living through 20 years of edits; Photosynthesis
retains far more ancient text (7,377 pre-2010 tokens vs 1,446), so its *average* churn looks higher.
**Conclusion:** churn on surviving tokens measures longevity, not contestation, and must not be used
as a standalone drift signal. The genuine contestation signal lives in the **deleted** tokens (old
stable text removed and *kept* removed) — which the hosted API does not expose → **001b (`wikiwho_rs`)**.

### What this does and does not prove
- **Does:** the editor-agnostic drift profile carries strong, cheaply-computable signal that separates
  a known-contested article from a stable one, on exactly the §10 axes (stability-lifespan collapse +
  low authorship diversity). No named list required.
- **Does NOT:** prove manipulation. ~89% recent text is also consistent with legitimate heavy editing
  after a major world event. Drift is necessary, not sufficient — the §10 *conjunction*
  (POV-reversal + removed-sourced + persisted-against-reverts) is still required, and needs
  deleted-token lifecycles (001b) + content analysis. n=2 here; the base-rate run over the ~10k slice
  + controls is the real test.

## Investigation Trail
1. Built `drift_profile.py`: recency, concentration, churn metrics from the 001a DuckDB.
2. First run: recency + concentration separated hugely; churn separated *backwards* — flagged as surprising.
3. Wrote a by-era churn query to test the age-confound hypothesis → **confirmed** (ancient tokens dominate churn).
4. Recorded the requirement: drop raw churn as a standalone signal; pursue deleted-token lifecycles in 001b.
