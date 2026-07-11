# wikiwho_rs (local WikiWho engine)

| | |
|---|---|
| **Project** | Open-source Rust reimplementation / local runner of WikiWho-style token tracking |
| **Repo** | https://github.com/Schuwi/wikiwho_rs |
| **Upstream idea** | Flöck & Acosta WikiWho (2014) |

## What it is

A **local** engine to compute token provenance from MediaWiki history XML dumps — no dependency on the hosted WikiWho cloud API for every article.

## Why it exists (for this project)

- Hosted WikiWho has **coverage gaps** and load limits on quieter articles.
- Corpus-scale L4 / batch work needs dump-based processing.
- WikiDrift contributed a **dump-parser fix** (entity-split text loss ~99% on markup-dense articles when `quick-xml` split character data at entities). After the fix, local matches hosted on neutral articles and reproduced benchmark verdicts 8/8.

## Relation to WikiDrift

Production path for scale and coverage: `wikidrift ingest` + local snapshots. Hosted API remains fine for popular/benchmark articles. Tooling dependency, not a research claim.
