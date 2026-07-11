# Persistent-word-revisions (PWR) and ownership / quality work (WikiSym 2009 line)

| | |
|---|---|
| **Authors** | Aaron Halfaker, Aniket Kittur, Robert Kraut, John Riedl (and related Halfaker et al. papers) |
| **Venue** | WikiSym 2009 (“A jury of your peers…” and PWR usage in the GroupLens Wikipedia line) |
| **Related** | Halfaker, Kittur & Riedl, “Don’t bite the newbies” (WikiSym 2011) — PWR/word as survival metric |

## What it is

**Persistent Word Revisions (PWR):** a content-survival metric — roughly, how many subsequent revisions a contributed word continues to appear in. Used as a quality/experience signal (editors whose words stick contribute durable content).

Also in this line: ownership effects (editors defending “their” text), revert impact on newcomers, etc.

## Method (sketch)

- Track words (or tokens) across the revision history.
- Count how long each contribution persists.
- Aggregate to editor- or edit-level quality proxies.

## Findings / contribution

Durable contributions are measurable without human labels. Survival metrics became a standard building block in Wikipedia research (quality, vandalism, editor experience).

## Relation to WikiDrift

**Direct basis for the L1 drift metric.** WikiDrift defines token weight \(w(t)\) from snapshots survived since origin and scores rewrites as **persistence-weighted content loss** (ratio classifies, PWR-mass ranks). The old 730-day “established deletion %” is the degenerate case \(w \equiv 1\). Grounding L1 in Halfaker/Adler-style survival is prior-art strategy ★#1.
