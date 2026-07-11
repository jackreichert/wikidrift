# About WikiDrift

<p class="summary">Measures how a Wikipedia article changed over time, and how language editions of the
same topic differ. Public data, revision IDs, reproducible diffs. Part of the
<a href="https://encyclopediae.org">encyclopediae.org</a> family — the diagnostic arm beside the
constructive project of encyclopedias from academic institutions.</p>

<p class="disclaimer">Candidates only — not conclusions.</p>

## What it measures

WikiDrift runs on public MediaWiki and WikiWho data. For each article it can report:

- **History:** when long-stable text was dismantled ([pivots](glossary.html#pivot)), with before/after
  diffs and which accounts removed or wrote the text
  ([persistence-weighted](glossary.html#pwr) displacement).
- **Other editions:** how English compares with editions such as Hebrew, Arabic, Polish, or German on
  [framing](glossary.html#framing) and [load-bearing facts](glossary.html#fact-divergence) — including
  whether English moved at the pivot, not only whether editions differ today.
- **Citations:** how the article's own source mix shifted across a rewrite
  ([from → to](glossary.html#source-change)), without reliability ratings on domains.

## How findings are produced

Articles are selected and scored from **content trajectory**, not from a roster of suspect editors.
Optional named lists can sit on top as overlays; they are not the foundation. A metadata pre-ranker
(byte displacement, no full text) decides what deserves a full token-level pass and routes addition- or
[churn](glossary.html#churn)-heavy cases to framing analysis when removal alone would miss them.

A large rewrite is a *change* signal. Base-rate runs show legitimate overhauls can out-pivot contested
topics. Direction is a separate layer (stance + cross-edition checks). Every page here is a
[lead](glossary.html#lead) with links back to underlying revisions.

## Limits in brief

Internal history cannot see bias that was there from the start with no rewrite to contrast
([born-framed](glossary.html#born-framed) / long-standing consensus). Those cases need external
reference — other language editions and related instruments. Attribution reports public actions only.

## How to use it

Open a finding, read the tabs, follow the [receipts](glossary.html#receipts). For layers,
validation lessons, principles, and citations, see [Methodology](methodology.html).
