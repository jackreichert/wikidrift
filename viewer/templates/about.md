# About WikiDrift

<p class="summary">Measures how a Wikipedia article changed over time, and how language editions of the
same topic differ. Public data, revision IDs, reproducible diffs. Part of the
<a href="https://encyclopediae.org">encyclopediae.org</a> family — the diagnostic arm beside the
constructive project of encyclopedias from academic institutions.
<a href="https://github.com/jackreichert/wikidrift/" target="_blank" rel="noopener">Open source on GitHub.</a></p>

<p class="disclaimer">Candidates only — not conclusions.</p>

## What it measures

WikiDrift runs on public MediaWiki and WikiWho data. For each article it can report:

- **History:** when long-stable text was dismantled ([pivots](glossary.html#pivot)), with before/after
  diffs and which accounts removed or wrote the text
  ([persistence-weighted](glossary.html#pwr) displacement).
- **Other editions:** how English compares with editions such as Hebrew, Arabic, Polish, or German on
  [framing](glossary.html#framing) and [load-bearing facts](glossary.html#fact-divergence) — including
  whether English moved at the pivot, not only whether editions differ today. In current runs,
  pivot-relative comparison uses one shared L1 pivot boundary across all compared editions.
- **Citations:** how the article's own source mix shifted across a rewrite
  ([from → to](glossary.html#source-change)), without reliability ratings on domains.

## How findings are produced

Articles are selected and scored from **content trajectory**, not from a roster of suspect editors.
Optional named lists can sit on top as overlays; they are not the foundation. A metadata pre-ranker
(byte displacement, no full text) decides what deserves a full token-level pass and routes addition- or
[churn](glossary.html#churn)-heavy cases to framing analysis when removal alone would miss them.

Default entity focus is self-determined from the article itself (controversy-agnostic), and pipeline runs
pass L2/L2.5 context into L5 so external checks are guided by observed shifts rather than fixed priors.

Edition selection is also defaulted for stability: runs use established topic-specific language editions,
and batch fact-check coverage applies an adaptive language cap per topic unless explicitly overridden.

A large rewrite is a *change* signal. Base-rate runs show legitimate overhauls can out-pivot contested
topics. Direction is a separate layer (stance + cross-edition checks). Every page here is a
[lead](glossary.html#lead) with links back to underlying revisions.

## Limits in brief

Internal history cannot see bias that was there from the start with no rewrite to contrast
([born-framed](glossary.html#born-framed) / long-standing consensus). Those cases need external
reference — other language editions and related instruments. Attribution reports public actions only.

## How articles were selected

The articles on this site are a validation set, not a curated accusation list. The thesis cluster
(Israel-Palestine, Holocaust in Poland) was chosen because those articles appear in independent external
sources — Wikipedia's own ArbCom arbitration findings, a peer-reviewed academic paper (Grabowski & Klein
2023), and a 2025 ADL report. Those sources define what the detector *should* find; the detector runs on
each article's own content independently and must reach the same conclusions without consuming any list.

Clean controls (Photosynthesis, Brontosaurus) and cross-domain contested articles (Climate change,
Abortion) were added to establish the base rate and catch false positives. The tool works on any article.

## How to use it

Open a finding, read the tabs, follow the [revisions](glossary.html#revisions). For layers,
validation lessons, principles, and citations, see [Methodology](methodology.html).
