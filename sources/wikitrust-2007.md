# A Content-Driven Reputation System for the Wikipedia (WikiTrust)

| | |
|---|---|
| **Authors** | B. Thomas Adler, Luca de Alfaro |
| **Venue** | WWW 2007 |
| **PDF** | https://archives.iw3c2.org/www2007/papers/paper692.pdf |

## What it is

**Content-driven reputation** for Wikipedia authors: reputation rises when an editor’s contributions **survive subsequent edits** (other people leave the text alone), and falls when contributions are removed. Later evolved into the **WikiTrust** system (trust coloring of text, vandalism detection follow-ons).

## Method (sketch)

- Diffs successive revisions to see whose text persists.
- Reputation is a function of **content survival**, not votes or social graph alone.
- Implicit idea: lasting text is a community signal of quality/acceptability.

## Findings / contribution

Showed that survival-based reputation correlates with quality and can support trust/vandalism tools without hand-labeled “good editors.”

## Relation to WikiDrift

**Conceptual ancestor of PWR-weighted loss.** WikiDrift’s L1 weights tokens by how long they survived (persistence), then measures displacement of that durable mass. WikiTrust used survival to score *authors*; WikiDrift uses survival to score *how much durable content a rewrite destroyed*. Same substrate, different product.
