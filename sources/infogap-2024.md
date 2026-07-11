# Locating Information Gaps and Narrative Inconsistencies Across Languages (InfoGap)

| | |
|---|---|
| **Authors** | Farhan Samir, Chan Young Park, Anjalie Field, Vered Shwartz, Yulia Tsvetkov |
| **Venue** | EMNLP 2024 |
| **arXiv** | https://arxiv.org/abs/2410.04282 |

## What it is

**InfoGap**: method to find **fact-level** gaps and inconsistencies across language editions (not only coarse corpus stats or bag-of-words).

Case study: ~2.7K LGBT biography pages across **English, Russian, French** Wikipedia.

## Method (sketch)

- Align and compare factual content across languages efficiently.
- Surface local document- and fact-level discrepancies at scale.

## Findings

- Large differences in which facts appear per language.
- In the case study, **negatively connoted** biographical facts more often highlighted on Russian Wikipedia.

## Relation to WikiDrift

Adjacent to **L5 instrument #2 (cross-edition claim divergence)**. InfoGap is NLP-heavy fact alignment; WikiDrift uses fixed load-bearing questions + LLM adjudication with as-of dates (e.g. KL Warschau victim counts). Same problem class: editions can disagree on *facts*, not only framing.
