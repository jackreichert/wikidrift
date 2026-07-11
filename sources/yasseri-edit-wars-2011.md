# Edit wars in Wikipedia (Sumi / Yasseri line) and mutual-revert controversy

| | |
|---|---|
| **Authors** | Róbert Sumi, Taha Yasseri, András Rung, András Kornai, János Kertész (and Yasseri et al. follow-ups) |
| **Key preprint** | https://arxiv.org/abs/1107.3689 (SocialCom 2011) |
| **Related** | Yasseri et al., PLOS ONE 2012 (dynamics of conflicts); multilingual controversy maps |

## What it is

Methods to detect **severe edit wars** and quantify how contentious a page is from the **revert graph** (mutual reverts between editors), without reading the prose.

Common product of this line: the **M-score** (mutual-revert controversy measure) — a scalar of how hard an article was fought over.

## Method (sketch)

- Identify reverts from revision metadata / hashes.
- Score pairs of editors who repeatedly revert each other.
- Aggregate to page-level controversy; study burstiness, talk length, multi-language patterns.

## Findings

- Severe conflicts are detectable and relatively rare; earlier work **overestimated** how contentious typical Wikipedia editing is.
- Controversy concentrates on a long-tail of pages; multilingual maps of “most contested” topics exist.

## Relation to WikiDrift

Implemented as `wikidrift mscore` (refined: registered editors + sustained mutual reverts).

**Use as context only — never a standalone flag.**

Project findings that match the literature’s limits:

- High M does **not** mean capture (Climate change: high war, benign quality rewrite).
- **Low** M on a large change (Nakba, KL Warschau ≈ 0) means *not fought over* → consensus addition or quiet change → route to L5, not “healthy.”
