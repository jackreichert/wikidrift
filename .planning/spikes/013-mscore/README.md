---
spike: 013
name: mscore
type: standard
validates: "Given an article's revision history, when the Yasseri mutual-revert M-score is computed, then contested articles score higher than smooth ones — as a corroborating controversy feature, not a bias detector"
verdict: VALIDATED (w/ significant caveats)
related: [008, 009]
tags: [mscore, revert, controversy, metadata, prerank]
---

# Spike 013: Yasseri Mutual-Revert M-score

Prior-art strategy #3 (previously unbuilt) + the §10 "persisted-against-reverts" idea. A
metadata-only conflict signal from the **revert graph** — no text, no WikiWho — so it sits
beside the pre-ranker (008). Identity-reverts via content hash (sha1); `M = E · Σ min(N_i,N_j)`
over mutual-revert pairs.

## How to Run
```
.venv/bin/python .planning/spikes/013-mscore/mscore.py
```
No API key. Fetches + caches full revision history (ids/ts/user/sha1) per article; recompute is instant.

## Results — VALIDATED (with significant caveats)

`refined` = registered editors only + sustained mutual reverts (≥2 each way).

| Article | revs | M (raw) | M (refined) | refined/rev |
|---|---|---|---|---|
| **Climate change** | 26,788 | 26,932,080 | 3,770,892 | **140.8** |
| **Zionism** | 12,060 | 1,667,351 | 131,747 | 10.9 |
| Israeli–Palestinian conflict | 9,419 | 757,170 | 57,874 | 6.1 |
| Water | 7,899 | 667,120 | 38,910 | 4.9 |
| Photosynthesis | 5,177 | 341,909 | 14,784 | 2.9 |
| **Warsaw concentration camp** | 1,056 | 168 | **0** | 0.0 |
| **Nakba** | 1,175 | 0 | **0** | 0.0 |

### Key findings
1. **Vandalism is a large confound; refine or don't use it.** Raw M over-rates high-traffic
   *vandalism magnets* — filtering anons + requiring sustained (≥2) mutual reverts cuts
   Photosynthesis ~23× and Water ~17×. Only the refined M is meaningful.
2. **M does NOT solve the base-rate problem.** **Climate change dominates** (refined/rev 140.8)
   because it genuinely *is* one of the most edit-warred articles — but its *recent* restructuring
   (the PWR false-positive from spike 009) was benign. So M **cannot demote** the Climate flag;
   controversy ≠ malice. This corrects the prior-art expectation that M would separate
   benign-vs-malicious change — it doesn't.
3. **The real value is corroboration + the LOW end.** High refined-M corroborates "contested"
   (Zionism, IPC). Crucially, **Nakba = 0 and KL Warschau = 0**: correctly saying those changes
   were *not* achieved by revert-warring — consensus-addition (Nakba) and quiet stable distortion
   (KL Warschau). So **low/zero M on an otherwise-flagged article is itself a lead**: "not an edit
   war → this is a born-bias / majority-consensus case → route to L5, not conflict analysis." That
   is precisely the mode a controversy measure structurally cannot judge, and L5 can.
4. **It does not, alone, implement "persisted-against-reverts."** M is *symmetric* warring; the §10
   clause is *directional* (did the NEW post-pivot text survive revert attempts?). A directional
   persistence measure is a separate, later refinement.

### How to use it (the honest role)
A **contextual feature beside the pre-ranker**, never a standalone flag: annotate a pivot with
refined-M as a "contested" badge; treat **low-M + high-PWR** as a stronger *route-to-L5* signal
(smooth imposition) than high-M (active conflict, still needs content adjudication).

## Investigation Trail
1. DB lacked sha1 → fetched + cached full history per article; computed raw M. Surprise: Climate
   highest, and Photosynthesis/Water (controls) high → suspected vandalism confound.
2. Tested registered-only + sustained-conflict (≥2): controls collapsed (~20×), Nakba/KL Warschau
   stayed 0, Climate stayed dominant. → M is a genuine controversy measure, confounded by vandalism,
   and orthogonal to malice. Verdict: VALIDATED as a corroborator, with the caveats above.
