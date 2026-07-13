# WikiDrift — Methodology & Discipline

> The *why* behind the code. Read this before drawing any conclusion from a finding. Every output of this tool
> is a **lead for a human to adjudicate, never a published verdict.** This document is a self-contained summary;
> the fuller design/decision record is maintained separately as the project's source of truth.

---

## 1. The thesis — two failure modes, two arms

Coordinated point-of-view editing distorts encyclopedic content in two structurally different ways, and each
needs a different instrument:

- **Capture by *change*** — an article was rewritten over time to become skewed. Detectable against the
  article's **own history** (the internal reference). This is WikiDrift's diagnostic core.
- **Capture by *origin / consensus*** — an article was shaped its intended way *from creation*, or a majority
  edits it smoothly with no contest, or a distortion simply stood for many years. These are **invisible** to a
  change detector, because "biased" has no meaning measured against a corpus's own history. Catching them needs
  an **external reference** (other-language editions; other authorities).

WikiDrift is the *diagnostic* arm (expose the incumbent's provenance). The complementary *constructive* arm is
plurality — many independent, transparent encyclopedias — which makes disagreement legible instead of asserting
one truth. WikiDrift's own limits (below) are the argument for that second arm.

## 2. The founding choice — judge the text, not the editors

The tempting approach is to take a list of "biased editors" and subtract them. We reject that as the
*foundation*: it inherits the list's circularity (co-editing/co-voting is what any dedicated topic editors
produce, with or without collusion — correlation, not coordination, and no base rate), and it makes the tool
"just another biased actor."

Instead: **measure an article's own edit trajectory.** A passage that survived years of readers and editors has
earned a **stability prior**; a rewrite that removes long-stable sourced text and *persists against reverts* is
an anomaly against that prior that is hard to explain innocently. A named list (anyone's) becomes one
*optional, sourced, swappable overlay* on top — never the basis for a flag.

## 3. The layers

Each layer answers a narrower question than the last. Lower layers work on any article from public data alone;
upper layers add language and meaning. **All are leads.**

| Layer | Question | Reference |
|---|---|---|
| **L1 — drift & pivots** | Was a long-stable "spine" destroyed, when, how much? Binary-search confirms the durable spine actually collapsed. | internal (own history) |
| **L1.6 — attribution** | Which edits removed the spine / wrote the replacement? *Action only, from public data.* | internal |
| **L2 — framing (stance)** | Did the meaning shift — not just the words — on a neutrality (NPOV) axis, whether by adding slanted text or removing critical text? | internal (temporal) |
| **L4 — discovery** | Where else to look? Use a confirmed article's actors as a *search prior* → re-test each candidate by its own content. | prior → internal |
| **L5 #1 — cross-lingual framing** | Do other-language editions frame it differently — and did English *peel away* from a prior cross-lingual consensus at the pivot? | **external** — standalone instrument (`wikidrift crosslingual`) |
| **L5 Framing Lite** | Pivot-gated: does the framing English lost survive in other editions' lead sections? Corroborates contested pivots. | **external** — `wikidrift framing` or `pipeline --framing`; SLATE + top-2 by length; lead sections only |
| **L5 #2 — fact/claim** | Do the editions disagree on load-bearing *facts* (as-of aware)? | **external** — standalone instrument (`wikidrift factcheck`) |
| **L5 #3b — citation-source change** | What did the article's *own citations* change **from → to** across the pivot? Reference-agnostic; **rates no source.** | internal (own refs) |

A **controversy signal** (Yasseri mutual-revert M-score) is context only: it cannot separate a genuinely
contested topic from a captured one, so a *low* score on an otherwise-flagged article is itself informative
(the change was made quietly → route to L5).

## 4. The metric that grounds L1 — persistence-weighted loss (PWR)

Drift magnitude is measured as **persistence-weighted content loss**: each token is weighted by how long it
survived, so destroying 20-year-stable text weighs far more than churning last month's edits. This is grounded
in the content-survival literature (Halfaker et al.; Adler & de Alfaro), computed offline from cached
snapshots. Episodes are **ranked by PWR-mass, age-agnostic** — a *long-standing* distortion is a first-class
find, never demoted for being old; recency is a descriptor, not a demoter. Robustness: snapshots are taken on
**persistent** revisions (size ≈ local median) so transient vandalism/blanking never reads as a rewrite.

## 5. The conjunction — so a finding stays a "smoking gun"

Change alone ≠ bias. A defensible L1 finding is a **stack**, not any single factor:

> long-stable · POV-reversing · removed previously-sourced text · persisted despite reverts · low authorship
> diversity on the change

Surface the conjunction; a single factor is noise.

## 6. Core discipline (the non-negotiables)

1. **Content-first, list-as-overlay.** Never flag by *who* edited; flag by trajectory. Lists are swappable overlays.
2. **Signal, not proof.** Every output is a lead; real events legitimately reshape articles. The researcher follows up.
3. **Necessary vs. sufficient, always separated.** "Text was replaced" ≠ "meaning reversed." "Editor acted" ≠ "editor had bad intent." State exactly which is established.
4. **Attribution of action, not intent.** Name who removed/added, from public data, with benign cases (bots, good-faith overhauls) visible. **Discovery ≠ attribution:** a graph lead points at an *article*; attribution independently names the *actual* actor — no "cluster captured this" claim is licensed.
5. **Robustness before trust.** A metric that false-positives on vandalism isn't a metric. Validate on a control before believing a positive.
6. **Base-rate before claims.** n=1 is a demo. A *designed* control slate (clean / forced-pivot / cross-domain-contested) is required to know a pattern is real, not generic.
7. **Neutrality of mechanism.** The tool must not encode which side is biased — not "academia is captured," not "this source is advocacy." It reports composition and change **as-is**; the reader judges. The moment it takes a side it becomes dismissible. (This is why L5 #3b **rates no source**, and why the scholarly-*consensus oracle* form of L5 #3 was not built.)
8. **Transparency is the product.** Reproducible from public data, method published, lists sourced and swappable, corrections possible.

## 7. Known limits (stated up front)

- **Born-biased blind spot.** L1 (change) and L2 (temporal shift) are structurally blind to articles born
  biased, majority-consensus bias, and long-standing distortions. That is exactly what the **external-reference**
  L5 layers exist to catch.
- **Change ≠ bias (the base-rate finding).** In a designed control run, the single largest rewrite was a
  *benign* one. The drift/pivot signal alone cannot distinguish capture from a legitimate large rewrite —
  which is the empirical mandate for L2 + L5.
- **Direction ambiguity.** The engine sees "long-stable text removed, changes beaten back," but cannot tell
  bias *injection* from bias *correction* — direction needs an external reference.
- **The external-reference asymmetry.** L5 #1/#2 work because every Wikipedia edition shares one substrate with
  *full public revision history*. A truly temporal comparison against academia/traditional encyclopedias would
  need *their* revision history, which they don't publish — reconstructing it is a project unto itself. That
  *only Wikipedia is fully auditable* is itself the case for transparent, versioned alternatives. (Hence
  L5 #3 ships as the reference-agnostic **3b**, not a consensus oracle.)
- **Attribution can mislead if unread.** A single "dominant drop" can be a restructure/merge/page-move, not a
  POV act. Read the diff before characterizing it.
- **Sockpuppet evasion.** L4 footprint traces editors by username to their destructive contributions elsewhere.
  Coordinated editing via rotating accounts is invisible — the per-account footprint collapses to noise. A
  co-edit clustering approach (detecting accounts that co-appear with unusual timing across flagged articles)
  would close this gap but is not yet implemented.
- **Cross-lingual agreed hoax.** L5 cross-lingual comparison detects *divergence* between editions. If all
  editions reproduce the same distortion in the same direction, comparison reads flat (agreement). Anchoring
  against an external reference corpus (academic sources, encyclopedias) is needed; not yet implemented.
- **LLM cultural bias.** Stance classifications (L2 NPOV-axis) are produced by a language model and may reflect
  training-data cultural bias on politically charged prose. A calibration baseline — repeated runs on
  known-neutral control articles — is needed to quantify a jitter floor. Not yet established.

## 8. What WikiDrift will not do

- Claim an editor had bad intent (it reports actions from public data).
- Assert "the neutral truth" (it makes disagreement legible).
- Treat a confirmed pivot as proven bias, or a cross-lingual/source divergence as a verdict.
- Rate any source as reliable or biased, or encode which side of a dispute is correct.
- De-anonymize a pseudonymous account to a real-world identity — that is out, categorically. (Naming a public
  *account* for its public *actions* is legitimate public data; asserting *capture* about it is a separate, high
  bar — see the discipline above.)

---

*Design + full decision record: maintained as the project's source of truth outside this repo; this file is the
in-repo summary that travels with the code.*
