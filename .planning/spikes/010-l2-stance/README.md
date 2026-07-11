# Spike 010 — L2 production stance classifier

**Goal:** turn "reframed" from a lead into a **measured** signal — the production L2 the methodology named
(VADER prototype in 006 was too weak). Classifies stance on an **encyclopedic-neutrality (NPOV) axis** —
critical / neutral / sympathetic toward a focal entity + an NPOV-departure flag — **not sentiment**
(Johnson 2025: sentiment conflates tone with viewpoint balance).

It's the discriminator the ★#3 benchmark says L1 lacks: a benign large change shows no directional stance
shift; a capture does.

## How it works
Per rsnap snapshot (spike 005): fetch wikitext via the Action API → strip to prose → keep focal-entity
sentences → classify with **Claude `claude-opus-4-8`** using **structured output** (`output_config.format`
json_schema, so every result validates). Emits a per-entity stance trajectory + directional-shift flag.
Focal entities are a transparent, per-article parameter (like 006's framing lexicon).

## Results (validated)
- **Zionism** (long history): directional shift **detected** — Zionism framing sympathetic → neutral
  (2002 → 2020), Israel sympathetic → neutral. L2 discriminates when pre-pivot history exists.
- **Nakba** (born-during-contested-period): **flat** — critical-of-Israel / sympathetic-to-Palestinians
  across every snapshot (2021 → 2025), NPOV-departure flagged throughout. There is no *shift* to detect
  because the article was **born with its framing** (established cohort ~8%, grown post-2021).

## The finding: L2-as-shift-detector hits the born-biased wall too
L2 is still **temporal + internal** — so, like L1, it's blind to articles *born* framed. Nakba's
consistent critical framing + persistent NPOV flags **are** a signal (this article carries a clear POV
throughout), but whether that POV is legitimate (scholarship does frame the Nakba as ethnic cleansing) or
capture is **undecidable from the article's own history**. That is precisely the job of **L5 (external
reference: cross-lingual / scholarly-source consensus)**. L2 measures and flags; L5 adjudicates direction.

## Honest limitations
- **Even-sampling can miss the target window.** The 4-snapshot Zionism sample stopped at 2020 and missed
  the 2024–26 pivot — it surfaced an *earlier* de-sympathization instead. Production should target the
  **L1 pivot window** (from spike 005), not evenly sample the whole history.
- **Stance ≠ bias** (Johnson 2025). A directional shift is a **lead** for a researcher; a real-world event
  can legitimately reshape framing. Never a bias verdict on its own.
- **Cost/latency:** one Claude call per snapshot. Bound snapshots (`--max-snaps`) and target the pivot
  window in production.

## Run
```
uv run --with anthropic python stance_classify.py "Nakba" --max-snaps 4
uv run --with anthropic python stance_classify.py "Zionism" --entities "Zionism,Israel,Palestinians" --max-snaps 4
```
Requires `ANTHROPIC_API_KEY` (or an `ant auth login` profile).
