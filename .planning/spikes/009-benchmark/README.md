# Spike 009 — Adjudicated benchmark (★#3)

**Goal (prior-art ★#3 / #10):** turn "works on Zionism" into calibrated precision/recall against an
*adjudicated* ground-truth roster. Design + full roster: `[[wikipedia-drift-benchmark]]` (vault).

## Method
Per roster article, combine two **offline** signals (no WikiWho; cached data only):
- **L1 drift + PWR-mass** — `validate_pwr.verdict_dict` (spike 005): the unconfirmed candidate verdict
  (PIVOT?/CREEP?/HEALTHY), episodes ranked by **PWR-mass** (age-agnostic); recency is a descriptor
  (recent vs standing), never a demoter. Binary-search confirmation (analyze.py) is a separate step.
- **pre-rank leads** — `prerank` (spike 008): `removal→PWR` / `addition→L2` routing.

Scored against category expectations, not "PIVOT = correct":
- **A must-flag (removal)** — PASS if flagged with PWR-mass ≥ `MASS_FLOOR` (age-agnostic — an old large capture still passes).
- **B must-flag (addition)** — PASS if the `addition→L2` lead is raised (L1 HEALTHY is *expected* — the
  born-biased blind spot; the addition vector is the catch).
- **D must-NOT-flag-as-bias** — benign change may register, but must be **demoted by low PWR-mass**
  (Water, Abortion). A *large* benign change (Climate) scores PARTIAL: correctly flagged as change, but
  L1 cannot tell benign from malicious → the L2/L5 gap, reported not hidden.
- **E clean** — PASS if HEALTHY.
- **C L5-gap** — born-biased / long-standing (KL Warschau): expected miss until L5 exists.

## First result (2026-07-07, cached subset)
| Category | Result |
|---|---|
| A must-flag (removal) recall | **3/3** (Zionism, Anti-Zionism, I-P conflict) |
| B must-flag (addition) recall | **1/1** (Nakba, via `addition→L2`) |
| D benign correctly demoted | **2/3** (Water 17.5k, Abortion 46k PWR — low mass) — **Climate = PARTIAL** (359k PWR) |
| E clean controls HEALTHY | **3/3** (Photosynthesis, Brontosaurus, Chess) |
| PENDING | ArbCom PIA5 set, Icewhiz (need data), KL Warschau (L5-gap) |

**Key finding:** L1 (PWR-mass) achieves recall on removal/addition candidates and correctly demotes
*low-mass* benign change, but **cannot separate large benign (Climate) from large malicious** — exactly
what L2 (stance) + L5 (external reference) exist to do. Recency is context only (recent vs standing); a
large **standing** distortion is a first-class find, never demoted by age. The benchmark quantifies the
gap that scopes the next build.

## Honest limitations
- **Offline / unconfirmed** — scores the coarse candidate verdict, not the binary-search-confirmed one.
- **Provisional threshold** — `MASS_FLOOR = 50_000` cleanly separates the current cases; recalibrate
  as the roster grows (esp. once section A/C get real data).
- **Section A/C undertested** — the must-flag set is thin until PIA5 / Icewhiz / KL Warschau are ingested
  (needs local `wikiwho_rs` on dumps). A single benchmark run does not certify the pipeline.

## Run
```
uv run python benchmark.py            # scored table + summary
uv run python benchmark.py --json      # + machine-readable JSON
```
