---
spike: 012a
name: crosslingual-align
type: standard
validates: "Given an English article + a configurable language set, when resolved via Wikidata sitelinks + per-language Action API, then correct per-language titles and current/as-of prose are fetched (en/he/ar and en/pl/de)"
verdict: VALIDATED
related: [010, 001a]
tags: [l5, crosslingual, wikidata, sitelinks, alignment]
---

# Spike 012a: Cross-lingual Alignment + Prose Fetch

First data layer of L5 (the external-reference bias layer). Before we can diff an
article's framing *across* language editions, we must reliably identify the SAME
article in each edition and pull its prose — current, or as of a timestamp so we
can later snapshot editions at an L1 pivot boundary.

## What This Validates

Given an English article title and a language set, when resolved via **Wikidata
sitelinks** and fetched from each **per-language Action API**, then the correct
per-edition titles and prose are retrieved for both topic triples (en/he/ar for
Israel-Palestine; en/pl/de for the Polish-Holocaust KL Warschau case).

## Research

- **Alignment:** Wikidata `wbgetentities` (sites=enwiki, props=sitelinks) is the
  authoritative article-identity map across editions — chosen over enwiki
  `prop=langlinks` (which can be stale/incomplete and lacks a stable Q-id anchor).
  The Q-id is also the future hook for per-language *entity-label* lookup in 012b.
- **As-of fetch:** Action API `prop=revisions` with `rvstart=<ts>&rvlimit=1` returns
  the last revision at or before a timestamp — the mechanism for pivot-relative
  (before/after) snapshots in 012c.
- **Prior art:** cross-lingual comparison itself is well-trodden (Manypedia,
  Omnipedia) but snapshot-only with no provenance/temporal join — that join is the
  novel L5 contribution. We don't reinvent the comparison, we anchor it to provenance.

## How to Run

```
.venv/bin/python .planning/spikes/012a-crosslingual-align/align.py                       # whole slate, current
.venv/bin/python .planning/spikes/012a-crosslingual-align/align.py "Nakba" en,he,ar
.venv/bin/python .planning/spikes/012a-crosslingual-align/align.py "Zionism" en,he,ar 2023-10-06T00:00:00Z
```

## What to Expect

Per edition: resolved title, revid, timestamp, prose char count, and a saved
`out/<slug>.<lang>.txt`. Plus a `out/<slug>.receipts.json` with the Q-id and full
provenance — the "receipts" the hosted viewer will render.

## Results

**VALIDATED** on the first pass, no iteration needed. All four articles resolved
across their editions, including RTL scripts and native titles:

| Article | Q-id | Editions (title → chars) |
|---|---|---|
| Nakba | Q3266633 | en `Nakba` 37.5k · he `הנכבה` 19.6k · ar `النكبة` 55.0k |
| Zionism | Q42388 | en `Zionism` 134.4k · he `ציונות` 51.2k · ar `صهيونية` 52.6k |
| Photosynthesis | Q11982 | en 51.8k · he `פוטוסינתזה` 17.4k · ar `تركيب ضوئي` 8.3k |
| Warsaw concentration camp | Q692676 | en 56.4k · pl `Warschau (KL)` 38.9k · de `KZ Warschau` 11.5k |

### Surprises / signal for downstream
- **Prose-size divergence is itself a lead.** The contested articles vary wildly by
  edition (Nakba: ar 55k vs he 19.6k — a ~2.8× gap; Zionism en 134k vs he/ar ~52k),
  while the neutral control Photosynthesis is smaller and less lopsided. Size is a
  crude proxy for *emphasis*, not framing — 012b's stance classifier is what turns
  emphasis into a directional signal — but the gap direction is worth watching.
- **Entity names differ per edition** (Israel = ישראל = إسرائيل), confirming the
  planned 012b fork: focal-entity filtering can't reuse English strings. Handle via
  Wikidata per-language labels vs. lead-section-wholesale (compare both in 012b).
- The KL Warschau editions carry meaningfully different sizes (pl 38.9k vs de 11.5k),
  the canonical born-biased / long-stable case L1+L2 cannot flag — the target for 012c.

## Investigation Trail
1. Built Wikidata-sitelink resolver + per-language prose fetch with an optional
   as-of timestamp. Ran the whole slate current-state → all editions resolved on the
   first run, RTL and Latin alike. No pivots hit yet (that's 012c). Verdict: VALIDATED.
