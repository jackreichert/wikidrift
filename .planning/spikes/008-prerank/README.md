# Spike 008 — Metadata-only candidate pre-ranking (★#2)

**Goal (prior-art ★#2 / scaling #8):** before spending expensive token-level PWR analysis (spike 005),
cheaply pre-rank articles from revision **metadata alone** — no article text, no WikiWho — so the
token engine only runs on real candidates. Attacks the scaling + hosted-API-flakiness pain directly.

## Approach
- **Data:** the columns Quarry / Wiki Replicas expose in bulk via SQL — rev `timestamp`, byte `size`,
  `actor`. We already cache the equivalent (`revisions.ts/user` + `rev_size.size`, fetched via the
  Action API), so the thesis is validated **offline**. Production sources these in bulk from Quarry /
  Wiki Replicas / dumps instead of per-article API calls.
- **Signal:** per 180-day bin, byte deltas split into `removed` vs `added`. `removed_bytes` is a cheap
  metadata analog of PWR-mass "spine destroyed" — it targets the removal thesis and separates a
  retrofit (large removals) from pure expansion (mostly additions).
- **Robustness (learned from 005):** raw byte churn is dominated by transient vandalism (blank −30k,
  restore +30k). Deltas are computed on a **rolling-median-smoothed** size series, which rejects
  blank/restore spikes and keeps only sustained change — the metadata analog of 005's persistent-
  revision snapshot. This was decisive: pre-smoothing, `removed ≈ added` everywhere and Water ranked
  #1 with 7.6M bytes of vandalism churn; post-smoothing the ranking became meaningful.

## Result (base-rate set, offline)
- **100% recall:** a cut at the lowest known-PIVOT removed-bytes (Anti-Zionism, ~55k) retains all five
  known PIVOTs (Zionism, Anti-Zionism, I-P conflict, Climate, Abortion); only two benign false
  positives pass (Chess, Water) — acceptable for a recall filter, and PWR + recency demote them.
- **Window targeting is a bonus that works for removal-heavy pivots:** Zionism's peak window resolves to
  `2024-12..2025-06` — the real pivot — so production can target dense snapshots there instead of
  sweeping 24 years of history.
- **Expansion vs retrofit:** Nakba (6k removed / 253k added) and Brontosaurus read as growth, not
  retrofit — the removed/added split does its job.

## Two-vector routing (removal + addition)
The removal metric is blind to **reframe-by-addition** (structural signal only exists for removals).
So the pre-ranker also tracks the peak *added* bin and raises a second lead type:
- **`removal→PWR`** — large, anomalous removals (retrofit candidate) → token engine (spike 005).
- **`addition→L2`** — a large, net-growth addition burst (added ≫ removed, above floor + anomaly) →
  entity-stance / stance classifier (spike 006). Growth alone is normal; a large *sourced* expansion
  can still reframe, so it is routed to L2, never dismissed as "growth."

On the base-rate set, **Nakba** is the sole `addition→L2` lead (rem-peak 6k, add-peak 253k, 51% editor
concentration, Sept-2023 window) — exactly the post-Oct-7 reframe-by-addition case the removal metric
labels "healthy." L2 confirmed a real framing shift there ("ethnic cleansing" 0 → dominant frame at the
Oct-7 boundary), a *lead* for a researcher + L5, not proof. Thresholds (`LEAD_FLOOR`, `ANOMALY_MIN`,
`GROWTH_RATIO`) are provisional — to be calibrated by the ★#3 benchmark.

## Honest limitations
- **Byte-neutral retrofits are under-located.** Where a recent pivot was more *rewrite* than net
  removal (I-P 2024, Anti-Zionism 2022, Climate 2020), the peak-removal window lands on an older,
  removal-heavier event. The articles still **rank high enough to be flagged** (recall intact); only
  the window hint is imprecise. This is expected — seeing byte-neutral displacement is exactly why the
  token-level PWR engine exists. A future add (edit-burst + editor-concentration, Yasseri-style) would
  catch byte-neutral contested rewrites and sharpen window targeting.
- **`anomaly` (× the article's own baseline) is a within-article flag, not a cross-article ranker** — a
  very stable article's small event looks huge relative to its ~0 baseline. Cross-article ranking uses
  absolute `removed_bytes`.
- **Chess is a weak "healthy" label** — it was never fully PWR-analyzed (only 5 early snapshots); its
  2009–10 removal event is plausibly a benign rewrite. Treated as an acceptable false positive.

## Necessary, not sufficient
A high pre-rank score is a **lead**: "spend a WikiWho/PWR pass here." It is not a verdict, and never a
bias claim — the token engine (005) confirms precision; L2/L5 address bias.

## Run
```
uv run python prerank.py                 # rank whole cached set
uv run python prerank.py "Zionism" ...    # specific articles
```
