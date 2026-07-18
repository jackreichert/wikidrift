# Mind Your POV: Convergence of Articles and Editors Towards Wikipedia's Neutrality Norm

| | |
|---|---|
| **Authors** | Umashanthi Pavalanathan, Xiaochuang Han, Jacob Eisenstein |
| **Venue** | CSCW 2018 / PACM HCI |
| **arXiv** | https://arxiv.org/abs/1809.06951 |

## What it is

Quantitative study of Wikipedia’s **NPOV tagging** as an intervention: does tagging reduce biased language in the article? Does it change editors’ future language?

## Method (sketch)

- Corpus of NPOV-tagged articles.
- Lexicons associated with biased / non-neutral language (including WP “words to watch”).
- **Interrupted time-series**: measure language before vs after the NPOV tag date.
- Also track individual editors who were corrected or participated in talk.

## Findings

- After NPOV tagging, **articles** show a significant drop in biased-lexicon language.
- **Editors** show little lasting change in their own lexicon use — content improves more than culture.

## Relation to WikiDrift

**Closest published neighbor on the “framing over time” half.**

| Mind Your POV | WikiDrift |
|---|---|
| Change-point = **known** (tag date) | Change-point = **discovered** (PWR pivot) |
| Lexicon bias markers | LLM NPOV-axis stance (L2) |
| No pivot attribution | Removal attribution and post-pivot contributors from provenance |

Cite as: temporal framing measurement works; do not assume the intervention date is known a priori for untagged capture.
