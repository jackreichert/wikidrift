# Adjacent advocacy, tooling, and shorter citations

Not full paper digests — pointers for things in the prior-art survey that are tools, reports, or secondary.

## Advocacy / list-first (method rejected as foundation)

| Item | What | Note for WikiDrift |
|---|---|---|
| **ADL, *Editing Hate* (2025)** | Report on alleged coordinated anti-Israel / anti-Jewish editing; named editor set, social-graph signals | Motivating use case for a **bounded topic**; method (list → conclusion) is the circularity WikiDrift avoids. Not an input list. |
| **Heritage “Project Esther” (2025)** | Watchdog / facial-recognition style efforts re Wikipedia antisemitism | Example of ethically fraught name-the-editors space |
| **US House Oversight inquiry (2025)** | Political pressure on WMF re bias | Context only |
| **Tech-for-Palestine exposés / Wiki-PR / paid editing cases** | OSINT and scandal literature | Sockpuppet / COI is a different problem (Solorio et al.; NSF paid-editing classifiers) |

## Tooling (reuse)

| Tool | Role |
|---|---|
| **Who Wrote That** / **WhoColor** | Browser-facing WikiWho visualization |
| **XTools** Authorship/Blame | Community analytics |
| **WikiBlame** | Interactive “which rev added this phrase” binary search — conceptual cousin of pivot binary search, single-string only |
| **Quarry / Wiki Replicas** | SQL over revision metadata (pre-rank: byte deltas, no text) |
| **ORES → Lift Wing** | Quality / revert-risk models — adjacent, not core |
| **pages-meta-history dumps** | Full history XML for local `wikiwho_rs` |
| **Wikimedia Enterprise API** | Commercial/stable access path (optional) |

## Other academic citations (one-liners)

| Work | One-line |
|---|---|
| **Editing Bursts (Springer 2020)** | Change-point / burst detection on edit *activity* + keyphrases — closest to pivot *timing* half, but activity ≠ content displacement; no provenance attribution. |
| **MultiWiki (ACM TWeb 2017)** | Multilingual Wikipedia comparison infrastructure — L5 family. |
| **Wikipedia Culture Gap (Frontiers 2018)** | Cross-cultural coverage gaps. |
| **Rozado / Manhattan Institute (2024)** | Contemporary political-bias measurement on Wikipedia text — cross-sectional slant, not temporal provenance. |
| **Steinsson (APSR 2024)** | Political science treatment of Wikipedia bias/neutrality. |
| **Kalla & Aronow (PLOS ONE 2015)** | Experimental / survey angle on Wikipedia trust/bias. |
| **Shi et al. (Nature Human Behaviour 2019)** | Collaborative knowledge production dynamics. |
| **Krebs et al. (2023)** | Counter-finding: Wikipedia not unusually slanted vs Britannica on studied topics — keep in framing. |
| **Solorio et al. (2013); Raszewski & De Kock (ACL 2025)** | Sockpuppet / multi-account detection — different problem. |

## Product-adjacent (not detectors)

| Item | Note |
|---|---|
| Brand reputation monitors | Adjacent market, not Wikipedia-specific provenance |
| Wiki Education + AI-text detectors (e.g. Pangram) | Detect AI-generated text, not narrative capture |
| Grokipedia / rival encyclopedias | Constructive alternatives (encyclopediae.org space), not monitors |

## Bottom line for composition novelty

No published system the survey found does all of:

1. Discover pivots from **content displacement** (not tag dates or edit bursts alone),  
2. **Attribute** the pivot via token provenance,  
3. Layer **direction** (stance + cross-lingual + optional sources),  
4. Keep the social graph **downstream** of content evidence only.
