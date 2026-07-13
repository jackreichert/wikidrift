# About WikiDrift

<p class="summary">WikiDrift shows how a Wikipedia article changed over time — and how versions in different languages tell the same story differently, or contradict each other. Almost everything you see is built from <b>public Wikipedia history</b> you can open and check yourself. WikiDrift leverages language models to compare <b>language editions</b>, how openings frame the topic, and whether basic facts agree.</p>

<p class="summary">You cannot honestly prove “Wikipedia was captured by bad actors” with a tool that won’t show its work — or that quietly takes a side. A project of <a href="https://encyclopediae.org">encyclopediae.org</a>. <a href="https://github.com/jackreichert/wikidrift/" target="_blank" rel="noopener">Open source on GitHub.</a></p>

<p class="disclaimer">Not built to pick a side or to hide the evidence. Judgments are for people — not for a black-box score.</p>

## In one sentence

WikiDrift finds and shows **when** a long-stable Wikipedia story was rewritten, **how much** changed, **who made the biggest cuts**, and whether **other languages still tell it differently**.

## What comes from public history vs a language model

**From public Wikipedia history alone** (no model):

- **Rewrite** — when a large share of long-stable text was replaced, and the before-and-after wording
- **Vocabulary** — which words became more or less common (simple counting across versions)
- **Citations** — which websites and books the footnotes cited before vs after (we do **not** rate sources)
- **Versions** — the exact Wikipedia revisions used, so anyone can verify
- Edit-fight and “who wrote today’s text” context, when shown

**From a language model comparing public text across languages** (only on some articles):

- **Framing** — how openings in different languages treat the same topic (for example more critical, neutral, or more sympathetic), with short quotes from each edition
- **Facts** — simple factual questions checked in each language, then agree / differ / contradict / not enough said

The model is doing the cross-language reading for you; the editions themselves are still public Wikipedia pages you can open. These steps only appear when they were run for that article. If you do not see a **Framing** or **Facts** tab, that check was not run (or not saved) for this page.

## What you will find on each article page

- **Start here** — a short briefing built from the checks that exist for this article.
- **Rewrite**, **Vocabulary**, **Citations**, **Versions** — public-history pieces (above).
- **Framing**, **Facts** — model-assisted pieces when they were run (above).

## What a rewrite window is (a “pivot”)

The **Rewrite** tab shows major **rewrite windows**: stretches of time when a large share of wording that had been stable for a long time was removed and replaced — and the new version **stuck** (not just a one-day vandalism blank that got fixed).

In the research notes and method docs, that window is often called a **pivot**. Same idea: dates (for example 2024-07 → 2026-01), roughly how much of the article changed, and a before-and-after view with struck-out old text and highlighted new text.

A rewrite window answers: **“When did the long-stable story flip?”** It does **not** answer: **“Was that flip good, bad, capture, or cleanup?”** That judgment is yours after you read the change.

Some pages have more than one window. Some have none large enough to show — those can still have gradual wording or footnote changes.

## What this is not

- It is **not** a “bias score” or a list of bad editors.
- A big rewrite is **not** automatically proof that something was captured or distorted. Legitimate cleanups and real-world events also produce big rewrites.
- Naming an account means “this public username made this public edit” — **not** “this person had bad intent.”

## Why that caution matters

Large rewrites are common. In our own tests, a normal science/history cleanup can look as large as a rewrite on a contested political topic. So the tool is built to **surface change and disagreement**, then leave judgment to a human reader who can open the diffs and sources. However, they aren't always done for those reasons, as you can see from our findings.

## How articles on this site were chosen

The topics here are a **test set**, not a hit list. We picked clusters that outside sources already discuss (for example Wikipedia’s own arbitration cases, academic papers, and public reports) so we could ask: does the tool notice the same places without being fed those lists?

We also include **control** topics (like Photosynthesis) and other hot-button subjects (like Climate change) to check that ordinary big edits do not get treated as automatic proof of wrongdoing.

The software itself can run on any English Wikipedia article. This site is just the published sample.

## How to use it

1. Open the [findings list](findings.html).
2. Pick an article and read **Start here** first.
3. Open **Rewrite** and actually read the struck-out / highlighted text.
4. Use **Framing**, **Facts**, and **Citations** only as extra context.
5. Click through to the live Wikipedia versions when you want proof.

For a longer explanation of the method, see [How it works](methodology.html).
