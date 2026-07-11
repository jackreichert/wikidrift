"""Every tuned threshold, in one small, frequently-edited file — the VOLATILE axis of config, kept apart
from the stable endpoints/paths/provider tables so a recalibration diff doesn't touch API contracts.

Values are unchanged from the spikes that validated them (005-analyzer, 008-prerank, 009-benchmark).
"""
# --- L1 drift engine thresholds (from 005-analyzer) -------------------------
MIN_COHORT = 500        # refine: min durable-spine tokens needed to binary-search a drop
MIN_MATURE = 15000      # only analyze once the article has >= this many tokens (skip stub-era churn)
MAG_FLOOR = 25.0        # min persistence-weighted loss % in an interval to consider a PIVOT
CONFIRM_DROP = 0.20     # binary search must confirm the durable spine declined by >= this fraction
CREEP_MEAN = 8.0        # sustained mean weighted-loss above this (no single pivot) = CREEP
DURABLE_Q = 0.50        # refine cohort = tokens present at interval start above this persistence quantile
RECENT_YEARS = 3.0      # episodes ending within this of the horizon are tagged "recent" (else "standing")
ELEVATED = 15.0         # per-interval loss % that starts/extends an episode (build_episodes)

# --- metadata pre-ranker thresholds (from 008-prerank) ----------------------
BIN_DAYS = 180          # calendar bin width for byte-delta binning
MIN_REVS = 50           # too little history to pre-rank meaningfully
SMOOTH_K = 5            # rolling-median half-window (revisions) — rejects transient blank/restore
LEAD_FLOOR = 50_000     # min peak-bin bytes to raise a lead (calibrate as roster grows)
ANOMALY_MIN = 5.0       # min × the article's own baseline to raise a lead
GROWTH_RATIO = 3.0      # addition lead: peak-added bin must be this-× removed (added >> removed)
# Relative-anomaly churn lead (Session 03 — the Palestinian-political-violence gap): a medium removal that is
# hugely anomalous vs the article's OWN baseline signals reframe-by-churn (remove some, add more) that the
# absolute LEAD_FLOOR misses and L1's ratio gate reads HEALTHY. Route to L2, not the PWR engine.
CHURN_ANOMALY = 10.0    # removal anomaly (× own baseline) to raise a churn→L2 lead even below LEAD_FLOOR
CHURN_MIN_BYTES = 15_000  # but require a non-trivial absolute removal (keeps PPV ~20.8k; drops a clean-control FP)

# --- benchmark (from 009-benchmark) -----------------------------------------
MASS_FLOOR = 50_000     # PWR-mass above this = a substantive drift lead (age-agnostic)
