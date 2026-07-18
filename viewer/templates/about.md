# About WikiDrift

<p class="summary">WikiDrift is a forensic tool to help researchers understand how a Wikipedia article has changed over time.</p>

Every Wikipedia page has a public history of all it's edits and tracks who did what. This project began with the question: can public edit history show that an article was tampered with? The short answer was, no. Revision history records what changed, when it changed, and which public account made the edit. It does not establish motive, identify a neutral version, or decide whether an edit improved the article. 

However, what the history *can* do is point out characteristics in the way an article was edited that *may* point to tampering. Finding a smoking gun doens't mean there's a body hiding somewhere. If you're in a shooting range, you'll find plenty of smoking guns, none of which was used to hurt a soul. 

WikiDrift looks for substantial changes to long-lived wording, then places those changes beside shifts in vocabulary, citations, cross-language framing, and basic factual claims. The result is a research lead with evidence a reader can inspect.

The initial proof of concept was built off the **Zionism** article, an article already discussed publicly in connection with editorial disputes, which made it useful for developing the method. Later, existing published approaches to text persistence, token authorship, controversy measurement, and cross-language comparison were implemented.

A project of [encyclopediae.org](https://encyclopediae.org), [open source on GitHub](https://github.com/jackreichert/wikidrift/).

## What WikiDrift does

- **rewrite detection** asks whether an article's established content went through substantial rewrites;
- **change interpretation** asks whether wording or stance changed directionally;
- **external comparison** asks whether a stable article differs from other language editions or from its own citation history;
- **discovery** uses an already detected change to decide which other articles are worth testing.

For the full sequence, inputs, and limitations, read [How it works](methodology.html).

## What a candidate rewrite window is

The **Rewrite** tab shows **candidate rewrite windows**: stretches of time when the snapshot scan measured unusually high loss of wording that had persisted across earlier snapshots. These published windows have not gone through the full revision-by-revision confirmation step.

The percentage shown is **persistence-weighted loss**. Wording that survived in more snapshots receives more weight, so the number is not a literal percentage of all article text changed. The page pairs that metric with dates and a before-and-after view of the selected snapshot revisions.

A candidate window asks: **“When might a substantial amount of long-lived wording have disappeared?”** It does **not** establish that one revision caused the loss or answer whether the change was good, bad, capture, or cleanup. That requires confirmation and human review.

Some pages have more than one candidate window. On other pages, rewrite analysis has not been exported; the site labels that as missing coverage rather than claiming no rewrite occurred.

## What this is not

- It is **not** a “bias score” or a list of bad editors.
- A big rewrite is **not** automatically proof that something was captured or distorted. Legitimate cleanups and real-world events also produce big rewrites.
- Naming an account means “this public username made this public edit” — **not** “this person had bad intent.”

## How articles on this site were chosen

The topics here are a **test set**, not a hit list. We picked clusters that outside sources already discuss (for example Wikipedia’s own arbitration cases, academic papers, and public reports) so we could ask: does the tool notice the same places without being fed those lists?

We also include **control** topics (like Photosynthesis) and other hot-button subjects (like Climate change) to check that ordinary big edits do not get treated as automatic proof of wrongdoing.

The software itself can run on any English Wikipedia article. This site is just the published sample.

## A useful reading order

1. Open the [findings list](findings.html).
2. Pick an article and read **Overview** first.
3. Open **Rewrite** and read the removed and added text.
4. Treat **Framing**, **Facts**, and **Citations** as separate context, not corroborating votes.
5. Follow the version links to Wikipedia when you need the original record.
