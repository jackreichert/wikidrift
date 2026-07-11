# Methodology

<p class="summary">How the detector works, how it was built, and what it does not claim.
Public data only; same inputs → same outputs.</p>

<p class="disclaimer">Candidates only — not conclusions.</p>

## Starting point

The project began as a token-level Wikipedia filter-mirror (show who wrote what; optionally
highlight contributions from a sourced list). Git looked like a natural substrate but was a poor fit:
line-level blame and no native "drop author X" model. **WikiWho-class token provenance into a columnar
store** became the engine; git/GitHub stayed useful for transparency and static hosting, not for blame.

Scope then narrowed around a concrete problem: coordinated point-of-view editing on contested topics.
A bounded topic slice is tractable. The decisive design choice was not which list to hard-code, but
**not to start from a list at all**.

## Content trajectory first

Some efforts begin with named editors and treat co-editing or aligned voting as evidence of
coordination. On contested topics, clustering is expected even without collusion, so that path is
circular. WikiDrift instead measures each article's **own edit history**. Named lists, if used, are
optional sourced overlays — never the foundation of a flag.

The structural signal of interest is **stable-then-retrofit**: long-surviving text is dismantled and
the replacement sticks. That is a [necessary condition](glossary.html#lead) for further review,
not proof of bias. Semantic direction and intent are separate questions.

## Two failure modes

Skew can arrive in different ways; they need different instruments:

- **[By change](glossary.html#capture)** — durable text was replaced and the change persisted.
  Read the article against its own past (L1–L2, then L5 for direction).
- **By origin / consensus** — framed from creation, or held steady by a quiet majority, with no useful
  earlier contrast. Internal history alone is blind. Needs an
  [external reference](glossary.html#external-reference) (other language editions, facts across
  editions, citation mix).

A third pattern sits between pure removal and pure addition: **reframe-by-churn** (article net-grows
while shedding unusual amounts of older text). L1 may read HEALTHY; metadata pre-rank routes those as
[churn → L2](glossary.html#churn) leads.

## Layers

Each layer answers a narrower question. Lower layers use public data only; upper layers add framing and
cross-edition comparison.

<div class="tablewrap"><table><thead><tr><th scope="col">Layer</th><th scope="col">Question</th>
<th scope="col">Method</th></tr></thead><tbody>
<tr><td><b>L1 — Drift &amp; pivots</b></td><td>When was durable text dismantled?</td>
<td><a href="glossary.html#pwr">Persistence-weighted loss</a> on the stable spine; coarse grid, then
binary-search for the pivot revision. Multi-episode; ranked by absolute PWR-mass.</td></tr>
<tr><td><b>Attribution</b></td><td>Who removed the old text / wrote the new?</td>
<td>Public revision and token-authorship data. Action only — not intent.</td></tr>
<tr><td><b>Pre-rank</b></td><td>Which articles deserve a full pass?</td>
<td>Metadata only (size / time / actor): rolling-median byte displacement; routes
removal → PWR, addition → L2, churn → L2.</td></tr>
<tr><td><b>L2 — Framing</b></td><td>Did stance on key entities shift?</td>
<td><a href="glossary.html#stance">NPOV-axis</a> ratings over time (not generic sentiment). Prefer
sampling the L1 pivot window.</td></tr>
<tr><td><b>L4 — Discovery</b></td><td>Where else should L1 look?</td>
<td>Seed from destroyers of a confirmed pivot; expand only via large removals elsewhere; re-test each
candidate on its own content. Graph never flags.</td></tr>
<tr><td><b>L5 #1 — Cross-lingual</b></td><td>Do other editions frame it differently?</td>
<td>Same stance read across editions, static and relative to the L1 pivot
(<a href="glossary.html#cross-lingual">native, no translation</a>).</td></tr>
<tr><td><b>L5 #2 — Facts</b></td><td>Do editions disagree on load-bearing facts?</td>
<td>Fixed questions, as-of dated answers, adjudicated for
<a href="glossary.html#fact-divergence">agree / differ / contradict</a>.</td></tr>
<tr><td><b>L5 #3b — Sources</b></td><td>How did the citation mix change across the pivot?</td>
<td>Cite-template types and domains (archive links unwrapped). Composition only —
<a href="glossary.html#source-change">no reliability ratings</a>.</td></tr>
</tbody></table></div>

Edit-war intensity ([M-score](glossary.html#conflict)) is context only. High controversy is
not capture; near-zero controversy on a large rewrite means the change was not fought over (route toward L5
when other signals fire).

## Stability prior and PWR

Text that survived years of editing has a [stability prior](glossary.html#stability-prior).
A large, lasting collapse of that spine is unusual enough to inspect. The L1 metric is
[persistence-weighted content loss](glossary.html#pwr) (Halfaker et al.; Adler–de Alfaro): each
token is weighted by how long it survived; classification uses the weighted loss ratio, ranking uses
absolute PWR-mass. Coarse passes run offline from cached snapshots.

One factor is not enough. Stronger cases stack signals ([conjunction](glossary.html#conjunction)):
long-stable, removed, meaning shift, persistence against reverts, concentrated authorship on the change.

## What validation taught

Early metrics broke in predictable ways; the fixes define the current method.

<div class="tablewrap"><table><thead><tr><th scope="col">Issue</th><th scope="col">Adjustment</th></tr></thead><tbody>
<tr><td>Raw churn higher on controls</td><td>Dropped standalone churn; age-confounded on surviving tokens</td></tr>
<tr><td>Spurious "pivots" from blanking</td><td>Snapshots on persistent revisions (size ≈ local median)</td></tr>
<tr><td>Blips labeled as pivots</td><td>Magnitude floor + binary-search confirmation</td></tr>
<tr><td>Percentage favored tiny old cohorts</td><td>Multi-episode ranking by absolute PWR-mass</td></tr>
<tr><td>Hosted API gaps / load</td><td>Retry/backoff; local <code>wikiwho_rs</code> for coverage</td></tr>
<tr><td>Medium reframe-by-churn missed</td><td>Relative-anomaly pre-rank lead → L2</td></tr>
</tbody></table></div>

**Base rate:** contested articles and some benign rewrites (e.g. Climate change quality overhauls) both
trigger pivots. L1 identifies *change*; L2 and L5 discriminate further. Real but tiny old rewrites on
clean articles are kept and demoted by mass — no suppression gate to make controls look perfect.

**Addition-side example (Nakba):** removal-based L1 can read HEALTHY when most of the article is new
growth. Byte growth can be citation-heavy while prose grows modestly; L2 can still show framing shift.
Those cases route to L5 rather than a clean bill of health.

## Principles

1. Article trajectory is primary; lists are optional overlays.
2. Outputs are leads for review, not final verdicts.
3. Separate necessary conditions (text replaced) from sufficient claims (meaning reversed; intent).
4. Validate on controls before trusting positives.
5. Require base-rate checks on designed control sets.
6. Cover removal, addition, and churn vectors.
7. Attribute public actions; do not infer intent.
8. Use the social graph only downstream of content evidence (L4 search prior).
9. Reproducibility from public data is part of the product.

## Key decisions

- Git rejected as blame/reconstruction engine (line-level mismatch).
- Hard-coded editor lists rejected as foundation.
- Default view highlights provenance; removal is a toggle, not the default.
- Hosted WikiWho for analysis; local `wikiwho_rs` for scale (parser parity certified).
- Rank by absolute PWR-mass; recency describes, it does not demote.
- L2 = NPOV-axis LLM stance (provider-agnostic); sentiment classifiers dropped.
- M-score = contextual corroborator only.
- L5 #3 = citation composition change, not a scholarly-consensus oracle (no source ratings).
- L4 discovery subtracts a **fixed** base-rate roster, never the growing cache.

## Established and open

**Established:** provenance pipeline; PWR metric; multi-pivot detection with persistent snapshots;
attribution; metadata pre-ranking; L2 stance; L5 framing + facts + sources; L4 first probe; local engine
parity with hosted on neutral articles; benchmark on adjudicated must-flag set (removal-oriented cases and
controls).

**Open / partial:** fuller L3 visualization across all articles; scaled L4 snowball; denser L2
shift-localization (where in time the stance moves); cross-encyclopedia external reference beyond
language editions; more benign-rewrite controls.

## Limits

Blind to bias with no historical contrast unless L5 has coverage. Direction is underdetermined from L1
alone (correction vs capture). Attribution names accounts and actions; a same-day "dominant drop" can be a
restructure — diffs should be read. Quiet editions and thin non-English coverage make some cross-lingual
results inconclusive (reported as such).

## Reproducibility

Findings point at public revisions under [receipts](glossary.html#receipts). Provenance from
WikiWho; timelines from the Action API; edition links from Wikidata. Framing and claim adjudication use a
language model with fixed JSON schemas (thinking disabled on classification calls so structured output is
reliable). Open-source tooling.

## What is novel

<p class="lead">Components already exist in the literature. The contribution is the join:</p>

- **Pivot + attribution.** Discover when the stable core collapsed from content displacement (not a known
  tag date or activity burst alone), then name who removed and who replaced at that revision.
- **Change vs origin.** Separate those modes; external reference is the answer to the internal blind
  spot, not a footnote.
- **Pivot-relative cross-lingual.** Not only "do editions differ today," but whether English moved
  relative to others *at the detected pivot*.

## Prior work

<p class="lead">Composition of established work, not a new authorship algorithm. Project notes live in
<code>sources/</code> in the repository.</p>

### Token authorship

- Fabian Flöck & Maribel Acosta, *WikiWho* (WWW 2014); *TokTrack*
  ([ICWSM 2017](https://arxiv.org/abs/1703.08244)).
- [wikiwho\_rs](https://github.com/Schuwi/wikiwho_rs) (this project contributed a dump-parser fix).

### Content survival

- Adler & de Alfaro, WikiTrust (WWW 2007).
- Halfaker et al., persistent-word-revisions (WikiSym 2009) — basis for [PWR](glossary.html#pwr).

### Framing / NPOV measurement

- Pavalanathan, Han & Eisenstein, *Mind Your POV*
  ([CSCW 2018](https://arxiv.org/abs/1809.06951)) — framing over time; change-point typically known a priori.
- Isaac Johnson et al., *Recommended Practices for NPOV Research on Wikipedia*
  ([2025](https://arxiv.org/abs/2510.21526)) — stance axis, not sentiment.

### Controversy

- Sumi / Yasseri et al., edit-war detection and mutual-revert measures
  ([arXiv:1107.3689](https://arxiv.org/abs/1107.3689); PLOS ONE line).

### Cross-lingual comparison

- Bao, Hecht et al., *Omnipedia* (CHI 2012); Massa & Scrinzi, *Manypedia* — snapshot
  external-reference prototypes.

### Empirical cautions and cases

- Greenstein & Zhu (AER 2012; MISQ 2018): article slant rarely shifts via revision — do not treat every
  rewrite as capture.
- Yang & Colavizza (2024): news-source composition as a measurable signal.
- Grabowski & Klein (2023) on Holocaust-history distortion — long-standing factual error as a target
  for the external-reference layer (see Warsaw concentration camp here).

### Not used as input

Advocacy reports that name editor clusters and treat co-editing as proof of coordination are not inputs
to any finding. They may be motivating context for topic choice; the detector does not consume their lists.

## Out of scope

No intent claims about accounts. No single "neutral truth" oracle. No automated "this article is biased"
label. The site shows where to look and how the numbers were computed.
