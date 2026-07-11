---
spike: 014
name: citation-claim-divergence
type: standard
validates: "Given the same article across editions, when comparing cited sources and load-bearing factual claims (with an as-of option), then factual/numerical distortion (KL Warschau) surfaces as cross-edition claim contradiction that stance divergence (012) misses"
verdict: VALIDATED (w/ caveat)
related: [012a, 012b, 012c]
tags: [l5, crosslingual, citation, claim, fact-distortion]
---

# Spike 014: Cross-edition Citation + Claim Divergence (L5 instrument #2)

Closes the gap instrument #1 (cross-lingual **stance**, spike 012) cannot: *factual/numerical*
distortion. Two signals comparing the same article across editions, with an as-of timestamp
(the temporal analogue of #1's pivot-relative mode):

- **Citation divergence** — cited-domain overlap (Jaccard) across editions. Pure parsing.
- **Claim divergence** — per-edition answers to load-bearing factual questions (Claude, native
  text), then a cross-edition adjudication: agree / differ / **contradict** / insufficient.

## How to Run
```
.venv/bin/python .planning/spikes/014-citation-claim-divergence/factcheck.py
```
Needs `ANTHROPIC_API_KEY`. Reuses 012a's per-edition titles; emits `out/<slug>.factcheck.json`.

## Results — VALIDATED (with caveat)

**Warsaw concentration camp / KL Warschau (en/pl/de) — the target:**
| | citation Jaccard | claim verdicts |
|---|---|---|
| **@ 2018** (pre-correction) | 0.04 (near-disjoint) | **differ on all 3** — victim count (en up to **212k** vs pl ~20k), victim identity, camp type (en extermination component vs pl/de rejected) |
| **now** (post-correction) | 0.12 | mostly **agree** (~20k, concentration/labor camp, extermination rejected) |

The ~200k / "death camp for ethnic Poles" myth surfaces as cross-edition contradiction **in 2018**
and has since converged — the temporal view shows the distortion *and its correction*. This is the
certified L5-gap miss (spike 009) that cross-lingual stance read flat.

**Photosynthesis control (en/he/ar):** claims **agree** (inputs/outputs, chloroplast). Citation
Jaccard low (0.08) *despite* agreement.

**Nakba (en/he/ar):** claims **agree** on facts (~750k displaced; ethnic cleansing) — its divergence
was in *framing* (instrument #1 caught it), not facts. Complementarity confirmed.

### Key findings
1. **Claim divergence catches fact distortion that stance misses.** KL Warschau 2018 contradicts across
   editions on the victim count / camp type; now converges. **Temporal (as-of) is essential** — the
   distortion is only visible historically, exactly like #1's pivot-relative mode.
2. **Citation Jaccard is a weak, confounded signal** — low even for the neutral control, because
   different-language editions cite local-language sources. Use as *context*, not a flag. **The claim
   adjudication is the reliable instrument.**
3. **The two L5 instruments are complementary and cover distinct capture modes:** framing capture
   (Nakba/Zionism → #1) vs factual distortion (KL Warschau → #2). An article can fail either axis
   independently (Nakba: framing-diverges, facts-agree; KL Warschau: facts-diverged, stance-agrees).
4. **Caveat on the adjudicator:** it graded 2018 KL Warschau "differ" (conservative) though the notes
   describe incompatible facts (212k vs 20k; extermination vs rejected). Production should treat a large
   *numeric* gap as "contradict" explicitly, not lean on the model's label alone.

## Investigation Trail
1. Built citation (domain-Jaccard) + claim (extract-then-adjudicate) over the shared editions, with an
   as-of fetch. Ran KL Warschau now + 2018, Photosynthesis control, Nakba.
2. 2018 KL Warschau diverged on every factual question (the myth); now converged → temporal claim
   divergence is the fact-distortion instrument. Citation Jaccard proved confounded by edition language
   (low even for the control) → demoted to context. Nakba facts agree (framing was #1's job). VALIDATED.
