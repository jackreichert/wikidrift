---
spike: 012c
name: divergence-signal
type: standard
validates: "Given per-edition stances, when diffed, then framing-contested articles diverge and the neutral control agrees; and across the L1 pivot, English peels away from the cross-lingual consensus (capture) vs editions moving together (legitimate)"
verdict: VALIDATED
related: [012a, 012b, 005]
tags: [l5, crosslingual, divergence, pivot-relative, thesis]
---

# Spike 012c: Cross-lingual Divergence Signal

Final step of the L5 cross-lingual instrument. Turns 012b's per-edition stances into
the L5 signal, in two modes: **static** (disagreement now) and **pivot-relative**
(does English peel away from the he/ar consensus across the L1 pivot?).

## What This Validates

- **Static** divergence separates framing-contested articles from a neutral control.
- **Pivot-relative** divergence distinguishes capture (English leaves a cross-lingual
  consensus across the pivot) from a legitimate event (editions move together).

## How to Run
```
.venv/bin/python .planning/spikes/012c-divergence-signal/divergence.py
```
Needs `ANTHROPIC_API_KEY`. Reuses 012b's saved stances (static) + 012a's as-of fetch
and L1's `drift.verdict_dict` pivot (pivot-relative). Emits `out/divergence.json`.

## Results — VALIDATED

**Static divergence** (0 = editions agree … 2 = maximal, critical-vs-sympathetic):

| Article | lead | focal | read |
|---|---|---|---|
| **Zionism** | **1.67** | **1.33** | strongly divergent — the sharpest born-divergence |
| **Nakba** | 1.00 | 0.33 | divergent (lead); focal only on Zionism-as-entity |
| **Photosynthesis** (control) | **0.00** | **0.00** | agrees perfectly — control passes |
| **Warsaw concentration camp** | 0.33 | 0.00 | ~flat → confirms the fact-distortion gap (012b) |

Clean separation: framing-contested (Zionism 1.67, Nakba 1.00) ≫ control (0.00), with
KL Warschau ≈ 0 (cross-lingual stance is blind to its numerical distortion — instrument #2/#3).

**Pivot-relative divergence** (English-vs-others gap, before → after the L1 pivot):

| Article | pivot boundary | before → after | read |
|---|---|---|---|
| **Zionism** | 2024-07-01 (**L1**: peak 68.4%, 824k PWR, age 0.5yr) | **0.17 → 0.67** | **PEELED AWAY** — capture lead |
| **Nakba** | 2023-10-01 (fallback; L1=HEALTHY, addition-side) | 0.50 → 0.50 | no net change — born-framed, not pivot-driven |

### The headline
**Zionism is the clean positive:** English *agreed* with he/ar before the L1 pivot (0.17,
near-consensus) and *diverged* after (0.67) — English left the cross-lingual consensus exactly
across the post-Oct-7 retrofit L1 already flags (68% PWR loss). That is the capture-vs-legitimate
discriminator L1+L2 alone could not make, now reconnected to L1's own detected pivot.

**Nakba** shows the complementary pattern: static divergence exists (1.00 lead) but the
English gap is *flat* across the boundary (0.50 → 0.50) — consistent with a **born-framed**
article (the divergence is inborn, not introduced at a pivot). Two modes, two failure modes.

## Investigation Trail
1. Static: spread metric over 012b's stances. Control 0.00, Zionism 1.67, Nakba 1.00,
   KL Warschau ~0 — the expected separation, incl. the documented KL Warschau gap.
2. Pivot-relative: pulled the L1 pivot from `drift.verdict_dict` (Zionism → real 2024-07-01
   episode; Nakba → HEALTHY, fell back to Oct-2023 as designed). Classified en/he/ar at both
   boundaries; measured the English-vs-others gap. Zionism peeled away; Nakba flat. VALIDATED.

### Caveats / next
- Snapshot granularity is ~annual, so Zionism's "before" (2024-07-01) sits mid-retrofit yet
  still showed near-consensus — a pre-Oct-2023 baseline would sharpen the peel further.
- Metric is deliberately simple (mean stance-sign spread / gap). Production should weight by
  NPOV-departure and evidence strength, and report both variants as the viewer's "receipts."
