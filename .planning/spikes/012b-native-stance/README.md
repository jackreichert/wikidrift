---
spike: 012b
name: native-stance
type: standard
validates: "Given non-English prose (he/ar/pl/de), when classified by the L2 NPOV classifier natively (no translation), then stances are coherent and cross-lingually comparable; and the focal-entity vs lead-section passage strategies are compared head-to-head"
verdict: VALIDATED (w/ caveats)
related: [010, 012a]
tags: [l5, crosslingual, stance, llm, native]
---

# Spike 012b: Native-Language Stance Classification

Second step of the L5 cross-lingual instrument. Runs the L2 NPOV classifier
(`src/wikidrift/stance.py`, unchanged) on 012a's per-edition prose **without
translation**, and compares two passage-selection strategies.

## What This Validates

1. Does Claude classify **native-language** prose (he/ar/pl/de) coherently on the
   NPOV axis, prompt-in-English / text-native, **no translation**?
2. Head-to-head: **focal** (sentences mentioning the entity, matched by native
   labels from Wikidata) vs **lead** (first ~6k chars, fixed comparable window).

## How to Run

```
.venv/bin/python .planning/spikes/012b-native-stance/native_stance.py            # whole slate
.venv/bin/python .planning/spikes/012b-native-stance/native_stance.py "Nakba"    # one article
```
Needs `ANTHROPIC_API_KEY`. Emits `out/<slug>.stance.json` per article (feeds 012c + the viewer).

## Results — VALIDATED (with caveats)

Stance abbreviations: `crit`/`symp`/`neut`/`abs`; `!` = NPOV-departure flag.

**Nakba (en/he/ar)**
| variant | Israel | Palestinians | Zionism |
|---|---|---|---|
| focal | crit/crit/crit — AGREE | symp/symp/symp — AGREE | crit/**abs**/crit — **DIVERGE** |
| lead  | crit/**neut**/crit — DIVERGE | symp/**neut**/symp — DIVERGE | crit/**neut**/crit — DIVERGE |

**Zionism (en/he/ar)** — divergent throughout; the sharpest born-divergence:
Zionism itself reads **neut(en) / symp(he) / crit(ar)** — legible disagreement between
independent authorities, exactly L5's target.

**Photosynthesis control (en/he/ar)** — **AGREE everywhere, all neutral**, both variants.
The method does not manufacture divergence on a neutral topic (low false-positive).

**Warsaw concentration camp / KL Warschau (en/pl/de)** — focal **AGREES** (Poland symp,
Germany crit, Jews symp); lead agrees except Germany (neut/crit/crit).

### Key findings
1. **Native classification is coherent — no translation needed.** Hebrew, Arabic,
   Polish, German prose all classified sensibly; the control stayed clean. ✓
2. **Focal vs lead materially differ, and measure different things.** *Lead* is a fixed,
   apples-to-apples window but sensitive to each edition's intro *style* (Hebrew Nakba
   leads clinically → reads neutral). *Focal* captures the article's whole-body treatment
   of the entity, but **selection-biases toward charged sentences** (it picks entity-mention
   sentences, which in contested articles skew charged → can inflate agreement). Recommend
   **reporting both**: lead as the fair cross-edition baseline, focal as corroboration.
3. **Cross-lingual STANCE catches framing divergence, not factual distortion.** Zionism/
   Nakba (framing) separate cleanly. **KL Warschau does not** — because its distortion is a
   *factual/numerical* myth (inflated victim count, "death camp for ethnic Poles"), not a
   stance-toward-entity shift. All editions are (rightly) critical of Germany / sympathetic
   to victims. **This refines the plan:** the certified L5-gap miss is *not* answerable by the
   cross-lingual stance instrument — it needs L5 instrument #2/#3 (cross-encyclopedia /
   scholarly-corpus + citation-source, or claim-level comparison). Cross-lingual is the right
   tool for *framing* capture (I-P), the wrong tool for *fact* distortion (Holocaust-in-Poland).

## Investigation Trail
1. Reused `classify()`/`focal_passage()` verbatim; added native-label lookup (entity's own
   article title per edition, via Wikidata) for focal filtering. Ran Nakba first: native
   classification coherent immediately; focal vs lead disagreed → confirmed the fork matters.
2. Ran the rest of the slate. Control clean (AGREE/neutral) — the critical sanity check.
   Zionism strongly divergent. KL Warschau agreed on stance → surfaced finding #3 (stance is
   blind to factual distortion). Verdict: VALIDATED for framing capture; scoped-out for fact
   distortion.
