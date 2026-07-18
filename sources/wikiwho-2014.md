# WikiWho: Precise and efficient attribution of authorship of revisioned content

| | |
|---|---|
| **Authors** | Fabian Flöck, Maribel Acosta |
| **Venue** | WWW 2014 |
| **PDF** | https://archives.iw3c2.org/www2014/proceedings/proceedings/p843.pdf |
| **ACM** | https://dl.acm.org/doi/10.1145/2566486.2568026 |

## What it is

Algorithm and model for **token-level authorship** on revisioned text (Wikipedia): for each word/token, who introduced it and in which revision — “git blame for prose,” done correctly at token granularity rather than line level.

## Method (sketch)

- Builds a **graph model** of revisioned content.
- Tracks tokens through add/delete/re-insert cycles more accurately than naïve string diffs.
- Optimized for precision *and* efficiency over full article histories (where earlier approaches were expensive or lossy).

## Findings / contribution

Establishes a practical, citable standard for Wikipedia token provenance. Later products (WhoColor, Who Wrote That, hosted WikiWho API, TokTrack dumps) sit on this foundation.

## Relation to WikiDrift

**Primary engine.** L1 snapshots, deleted-token lifecycle, removal attribution, post-pivot contributors, and L3 redline authorship all depend on WikiWho-class provenance. WikiDrift does not reinvent attribution; it **joins** provenance to unsupervised pivot discovery and downstream layers.
