# Glossary

<p class="summary">Terms used on article pages and in the methodology.</p>

<dl class="glossary">
<dt id="lead">Lead</dt><dd>A candidate worth inspecting. Not proof of bias or bad faith.</dd>
<dt id="drift">Drift</dt><dd>Narrative or content shift over successive edits.</dd>
<dt id="pivot">Pivot</dt><dd>A window where a large share of long-standing text was replaced at once.
Discovered from content displacement (not only from edit bursts or a known tag date). Shown on the Diff
tab timeline.</dd>
<dt id="retrofit">Stable-then-retrofit</dt><dd>Long-surviving text dismantled and the replacement sticks.
Structural signal for L1; still not proof of bias.</dd>
<dt id="framing">Framing</dt><dd>How a subject is portrayed (critical / neutral / sympathetic) beyond
bare facts. Editions can frame the same topic differently.</dd>
<dt id="stance">Stance / NPOV axis</dt><dd>Framing scored critical–neutral–sympathetic. Not positive /
negative sentiment (tone ≠ viewpoint balance).</dd>
<dt id="cross-lingual">Cross-lingual comparison</dt><dd>Same topic across language editions. Can be
static (today) or <i>pivot-relative</i> (before vs after a detected rewrite). Disagreement is a signal with
more than one possible cause. In current runs, pivot-relative mode uses one shared L1 pivot boundary
across all compared editions.</dd>
<dt id="fact-divergence">Fact divergence</dt><dd>Incompatible load-bearing claims across editions (e.g.
different counts). Separate from framing: facts can agree while frames differ.</dd>
<dt id="lexical-drift">Lexical drift</dt><dd>Shift in term distribution between two revision windows,
summarized with Jensen-Shannon divergence and relative term keyness (smoothed log-odds). A signal for
context shift, not a verdict.</dd>
<dt id="source-change">Citation-source change</dt><dd>How an article's own citations shifted
<i>from &rarr; to</i> across a major rewrite: domains added/dropped and mix of journal / news / book /
web. No reliability labels on sources — composition only.</dd>
<dt id="blame">Blame</dt><dd>Per-span authorship: which account introduced which text (like VCS blame).</dd>
<dt id="concentration">Editor concentration</dt><dd>Share of current text written by a small set of
accounts (e.g. top-10), plus distinct-editor count. Context only.</dd>
<dt id="born-framed">Born-framed</dt><dd>Strong framing from creation, so there is no earlier baseline
to compare. Needs an external reference, not a change detector alone.</dd>
<dt id="churn">Reframe-by-churn</dt><dd>Article net-grows while shedding unusual amounts of older text
relative to its own baseline. May look HEALTHY to pure removal-ratio L1; routed to L2 via pre-rank.</dd>
<dt id="control">Control</dt><dd>A neutral topic (e.g. Photosynthesis) used as a sanity check.</dd>
<dt id="conflict">Conflict weight / M-score</dt><dd>How hard the article was edit-warred (mutual-revert
style measures). Contested ≠ biased. Low score on a large change means the rewrite was not fought over.</dd>
<dt id="revisions">Revisions</dt><dd>Exact revision IDs and dates behind a finding.</dd>
<dt id="stability-prior">Stability prior</dt><dd>Long-surviving text is treated as sticky by default;
a lasting collapse of that text is the anomaly the L1 layer targets.</dd>
<dt id="pwr">Persistence-weighted loss (PWR)</dt><dd>Rewrite magnitude: each token weighted by how long
it had survived. Deleting old text counts more than churning recent text. Based on published content-survival
metrics (Halfaker; Adler–de Alfaro). Ratio classifies; absolute mass ranks.</dd>
<dt id="capture">Change vs origin</dt><dd><i>By change:</i> rewritten over time (this tool's L1 core).
<i>By origin:</i> framed from the start or held steady without a rewrite — needs an external reference.</dd>
<dt id="base-rate">Base rate</dt><dd>Large rewrites are common and often legitimate. One rewrite in
isolation is not diagnostic; compare against controls and other layers.</dd>
<dt id="external-reference">External reference</dt><dd>Something outside the article's own history —
other language editions, other encyclopedias — used when internal history has no contrast.</dd>
<dt id="conjunction">Conjunction</dt><dd>Multiple stacked signals (stable → removed → meaning shift →
persisted → concentrated authorship). Single factors are weak.</dd>
<dt id="l4">L4 discovery</dt><dd>Use destroyers of a confirmed pivot only as a search prior; re-test each
candidate article on its own content. Graph membership never flags an article.</dd>
</dl>
