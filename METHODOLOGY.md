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
| **L2a — additive trajectory** | Which sentence-level claims were added, removed, retained, or relocated across exact stable revisions, and which additions remained standing? | internal — `wikidrift framing-trajectory`; deterministic research lead |
| **L4 — discovery** | Where else to look? Use a confirmed article's actors as a *search prior* → re-test each candidate by its own content. | prior → internal |
| **L5 cross-language stance comparison** | Does entity-relative stance differ across editions, and did English *peel away* around the pivot? | **external** — standalone instrument (`wikidrift crosslingual`) |
| **L5 cross-language lead comparison** | Which concrete claims, omissions, or contradictions differ across edition leads? Prefer the exact pair from a fresh persisted L1 confirmation, then fall back to the coarse candidate or current leads. | **external** — `wikidrift framing` or `pipeline --framing`; category slate + top-2 by length; pivot-relative, candidate-relative, or static |
| **L5 #2 — fact/claim** | Do the editions disagree on load-bearing *facts* (as-of aware)? | **external** — standalone instrument (`wikidrift factcheck`) |
| **L5 #3b — citation-source change** | What did the article's *own citations* change **from → to** across the pivot? Reference-agnostic; **rates no source.** | internal (own refs) |

A **controversy signal** (Yasseri mutual-revert M-score) is context only: it cannot separate a genuinely
contested topic from a captured one, so a *low* score on an otherwise-flagged article is itself informative
(the change was made quietly → route to L5).

## 4. The metric that grounds L1 — persistence-weighted change (PWR)

Drift magnitude is measured as **persistence-weighted content loss**: each token is weighted by how long it
survived, so destroying 20-year-stable text weighs far more than churning last month's edits. This is grounded
in the content-survival literature (Halfaker et al.; Adler & de Alfaro), computed offline from cached
snapshots. Episodes are **ranked by PWR-mass, age-agnostic** — a *long-standing* distortion is a first-class
find, never demoted for being old; recency is a descriptor, not a demoter. Robustness: snapshots are taken on
**persistent** revisions (size ≈ local median) so transient vandalism/blanking never reads as a rewrite.

The initial sweep measures every covered interval symmetrically: persistence-weighted loss, standing gain,
retained mass, and the paired portion of concurrent loss and gain. A paired change is a **replacement lead**,
not proof that one passage semantically replaced another. Loss or gain at 15%, or paired change at 10%, is
retained as an anomaly. Absolute PWR mass sets review priority (10,000 review; 50,000 high) but never suppresses
an anomaly. An extreme percentage can also receive high priority so small articles are not hidden.

The 1,000-token exact-check floor follows from the confirmation test rather than article maturity: the durable
spine uses the top persistence half and needs at least 500 tokens, so a 1,000-token start is the smallest interval
that can satisfy that evidence requirement by construction. Smaller intervals remain visible as descriptive
evidence but cannot receive an exact durable-spine verdict.

Loss confirmation remains two-pass. The primary pass groups sharp intervals with a 25% peak; when none confirms,
a rolling pass tests direct cohort loss across approximately twelve months at 20%. All candidates are retained
and checked when evidence permits. PWR mass orders review; it is not a rolling-pass admission rule. Both sources
must still show at least a 20% collapse of the durable spine in underlying revisions. Gain and replacement leads
remain provisional until dedicated semantic confirmation exists.

Each candidate sent to revision-level confirmation is retained in the confirmation artifact with its source
pass, coarse interval, PWR mass, peak loss, exact drop when measurable, decision, and rejection reason. A
candidate is rejected when its durable-spine drop is below the 20% confirmation threshold or when the exact
revision pair cannot be resolved from sufficient evidence. Candidates not checked are not labeled rejected.

### Exact-event editor-concentration calibration

Every fresh confirmed event can expose transparent structure without assigning motive: durable-spine drop,
PWR mass, exact duration, top-editor removal and replacement shares, top-two removal share, and whether the
same public account is the top associated editor on both sides. These values are recomputed from persisted
per-editor token counts rather than trusted as cached percentages.

The offline checkpoint command reads article-owned shards and reports one row per exact event:

```text
wikidrift calibrate-concentration .planning/spikes/data/articles --json
```

Only confirmations matching the shard's current corpus horizon and threshold contract are eligible. Missing,
stale, malformed, or unattributed events are listed as exclusions rather than silently dropped. The report is
deliberately **unlabeled** (`labels_enabled=false`): no `concentrated_rewrite_lead` or burst threshold becomes a
default until confirmed concentrated events, distributed rewrites, reverts or vandalism recovery, article
splits or merges, copyedits, and neutral controls have been adjudicated and compared. Raw measures remain the
primary evidence after any future rule is calibrated.

The report also exposes `calibration_ready` and explicit `calibration_blockers`. Editor-share features need at
least two observations and observable variance before threshold calibration can proceed. Confirmed stable
boundaries now resolve to the complete ordered revision sequence between them. Adjacent token-state differences
record gross additions, removals, and restorations; final-boundary differences separately record net-standing
removals and replacement text. A reverted contribution remains visible in gross activity but does not inflate
standing participation. Every displayed share is recomputed from per-revision rows. Concentration labels remain
disabled until adjudicated concentrated rewrites, distributed rewrites, reverts, vandalism recovery, splits or
merges, copyedits, and neutral controls produce discriminating distributions.

### Editorial-process context

Process context is an opt-in, descriptive evidence family for fresh exact events. Bounded Action API retrieval
records edit summaries, tags, SHA-based restoration relationships, talk-page activity, protection state, page
operations, and selected dispute templates when available. Every displayed revision or log item links to its
public oldid or log receipt. Each family reports `observed`, `not_observed`, or `unavailable`; no observed talk
activity means only that the bounded query found none, not that discussion did not occur elsewhere.

Process context cannot change L1 confirmation and is excluded from corroboration counts. Edit summaries,
reverts, protection, dispute templates, and talk activity can identify alternatives worth inspecting, but they
do not establish identity, coordination, motive, ownership, factual quality, bias, or misconduct.

### Confirmed cross-article discovery

L4 builds an offline graph only from fresh confirmation artifacts whose threshold contract and corpus horizon
match their article-owned shard. Its nodes are literal public account names from structured exact-event removal
attribution; bots, anonymous IPs, and hidden names are excluded, and accounts are never merged or inferred to
share an identity. The graph ranks repeated article breadth, confirmed-event count, and raw removed-token count:

```text
wikidrift confirmed-graph .planning/spikes/data/articles --json
```

This graph is a search prior, not a finding. The network-backed `discover` command may use an eligible editor's
public removal footprint to select additional articles, but each candidate must independently complete full L1
analysis and reach exact `confirmed` status before appearing in a rewrite-lead list. A coarse `PIVOT?`, graph
membership, or shared account can never promote an article. Exact confirmation establishes durable content
change only; it does not establish bias, motive, policy violation, or coordination.

### Analysis across confirmed events

Downstream analysis is event-complete rather than article-singleton. Every fresh confirmed revision pair receives
its own vocabulary and citation analysis. When requested, framing and fact comparison also run independently for
every pair. Vocabulary, citations, and framing bind to the exact before and after revisions. Fact comparison asks
a different question: it compares editions as of the exact post-event timestamp and is not presented as a
before/after fact change.

Layer artifacts retain one record per event under `episodes`, identified by the exact
`<before_revid>-<after_revid>` pair. Each record reports whether its evidence is available. Retrieval or model
failure for one event is retained as unavailable and cannot suppress completed sibling events or another layer.
Top-level compatibility fields may mirror one available event, but the event collection is authoritative for
multi-event analysis.

### Publication and schema backfill

Published rewrite panels consume the exact confirmation artifact before any legacy coarse pivot export. A fresh
confirmed artifact supplies the exact revision IDs, timestamps, duration, durable-spine drop, PWR mass, corpus
horizon, and structured attribution receipt. A fresh rejection suppresses coarse pivot output; stale or unavailable
evidence renders as unavailable rather than healthy. Schema-v3 attribution resolves every revision between the
stable boundaries and separates gross additions, removals, and restorations from net-standing removals and
replacement text. Public wording describes observable account/revision associations only.

Confirmation artifacts use schema version 2 and may embed schema-v3 attribution receipts. Existing fresh confirmed
shards can upgrade attribution offline without rerunning L1 or changing the confirmed boundaries:

```text
python tools/cover_missing_topics.py --all-shards --mode attribution --execute --jobs 3 --no-resume
```

The selector opens each article-owned corpus read-only and admits only confirmations matching the current corpus
horizon and threshold contract. Workers remain article-isolated and bounded; stale, rejected, malformed, or missing
artifacts are not rewritten.

Neutral editorial-process context is a separate opt-in backfill:

```text
wikidrift backfill-process-context "Article title"
```

Each evidence family preserves exact public links and distinguishes `observed`, `not_observed`, and `unavailable`.
Process context cannot change confirmation or enter corroboration counts. Existing shards still require explicit
attribution and process-context backfill before the viewer can display these Wave 3 receipts.

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
- **Rolling-pass calibration is preliminary.** The 20% / 50,000-PWR rolling candidate gate recovered a
  confirmed case missed by the primary interval threshold, while the unchanged revision-level gate retained
  the final precision check. The existing offline benchmark does not yet score this fallback end to end.
  Measure its recall and false-candidate load on a fixed positive/control slate before treating these candidate
  thresholds as generally calibrated.
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
  training-data cultural bias on politically charged prose. WikiDrift retains exact passages and repeated raw
  runs around apparent transitions, and separates model instability from text change. Control-set calibration
  is still required before changing the documented agreement and evidence-coverage floors.

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
