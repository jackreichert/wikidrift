# How it works

<p class="summary">A plain explanation of what WikiDrift measures, how a page is built, and where the method stops.</p>

<p class="disclaimer">Every result is something to inspect — not a verdict of guilt or bias.</p>

## The question we started with

Can public edit history show when a Wikipedia article’s long-stable wording was substantially
replaced, without starting from a list of editors or articles assumed to be problematic?

That is the core of WikiDrift. Prior lists may provide validation cases, but they do not determine
what the detector finds. The article’s own history supplies the primary evidence.

## Two different problems

Problems on Wikipedia do not all look the same:

1. **The story changed later.** Text that had sat for years was removed, and the new version stuck.
   You can often see that by comparing the article to **its own past**.
2. **The story was always framed that way.** There is no earlier “neutral baseline” in the history.
   Comparing only to the past will miss it. You need **something outside the English history** —
   usually other language editions, or a careful fact check across languages.

WikiDrift is strongest on the first problem. The second is only partly covered (other languages and
shared facts), and honestly so.

## What “a finding” means

A finding is a **lead**: “look here.”

By itself, it is **not**:

- proof that editors colluded,
- proof that the old text was more accurate,
- proof that the new text is wrong,
- or a score of how biased an article is.

Big, honest rewrites happen all the time (style overhauls, new sources, real-world events). That is
why we also test quiet science articles: so we know what ordinary change looks like.

## How we read one article’s history

### 1. Track who wrote which words

Wikipedia keeps every past version. Tools such as WikiWho can say, for each bit of text, which
revision introduced it. We store those snapshots so measurements are repeatable.

### 2. Prefer long-lived text

Words that survived for years of readers and editors count more than last month’s churn. Deleting a
sentence that lived for a decade is a bigger signal than deleting something that appeared last week.

### 3. Find candidate change windows

We look for stretches of time where a large share of that long-lived text disappeared and stayed
gone (not a one-day vandalism blank that was immediately fixed). Those windows appear on the site as
**Rewrite** pages you can open and read.

### 4. Show before and after

For those windows we build a tracked-changes view: removed text struck out, new text highlighted.
Where we can, we also note which accounts added the new wording.

### 5. Note who wrote today’s text

Separately, we describe how much of the **current** article comes from a small set of accounts, and
how much of it is recent. That is background — concentrated authorship is common and is **not** by
itself evidence of a plot.

## Words, sources, and “how hard was the fight?”

Besides the rewrite itself, we often show:

- **Vocabulary** — which words became more or less common across the rewrite window. Useful for
  noticing topic or tone shifts; not a moral score.
- **Citations** — which domains and citation types (book, news, journal, web) grew or shrank. We
  report the mix **as-is**. We never label a domain “reliable” or “biased.”
- **Edit fights** — whether the article saw lots of mutual reverts (editors undoing each other).
  A quiet rewrite can matter *more*, not less: it means the change was not loudly contested on the page.

## Other languages

English is only one Wikipedia. For many topics we also compare:

- **Framing in the opening** — does the lead sound more critical, neutral, or sympathetic toward the
  same subject in Hebrew, Arabic, German, Polish, and so on?
- **Basic facts** — for a short list of load-bearing questions (dates, places, counts), do editions
  agree, differ, or contradict?

When we can, we also ask a sharper question: did English **move away** from other languages around
the same time as a big rewrite — or have the languages disagreed for a long time?

Disagreement has many causes (different audiences, different sources, translation lag, real debate).
It is a reason to read carefully, not a scoreboard.

## Following the trail of large deletions

After a confirmed big rewrite, we can look at where the same accounts made **other large deletions**
on other articles, then **re-check each candidate on its own history**. Appearing in that search does
not create a finding; the second article must independently meet the same change criteria.

## What we never do

- Claim an editor’s private motives.
- Publish “the neutral truth.”
- Rate news sites or books as good or bad.
- Treat a rewrite as automatic proof of capture.
- Tie a public username to a real-world identity.

## Why these topics appear on the site

This site is a **validation sample**, not a complete encyclopedia audit.

- Some topics appear because **outside** reports and research already discuss them. Those sources help
  us define what a working detector *ought* to notice — they are **not** fed into the detector as a
  list of people to watch.
- **Quiet control** articles (for example Photosynthesis) check for false alarms.
- **Other contested** topics (for example Climate change) check that large legitimate rewrites still
  look like *change*, not automatic scandal.
- Some pages are expected to be hard for history-only tools (story shaped from the start). Those show
  why other-language checks matter.

Any English Wikipedia article can be run through the same pipeline. What you see here is the published
test set.

## Lessons that shaped the method

Early versions made familiar mistakes. The fixes are part of the product:

| What went wrong | What we do now |
| --- | --- |
| Counting every edit as drama | Weight long-lived wording more than short-lived churn |
| Vandalism blankings looking like rewrites | Ignore short-lived blankings; require the change to stick |
| Tiny old pages looking “more rewritten” in percentages | Rank by absolute amount of long-lived text lost, not just percentages |
| Missing articles that *grew* while swapping tone | Also look at vocabulary, framing, and other languages — not only pure deletion |
| Treating one big rewrite as proof | Always compare with control topics; never publish a bias label |

One result that still guides us: a benign overhaul on a well-known science topic can outrank a
political rewrite on raw size. That is why this site always says **change first, judgment later**.

## Reproducibility

- Article text and revision IDs come from public Wikipedia APIs and related open tooling.
- Framing and fact checks, when present, use a language model with a fixed question format — still
  treated as leads, not oracles.
- The site is static HTML generated from saved result files; you can rebuild it from the
  [open-source repository](https://github.com/jackreichert/wikidrift/).

## For readers who want the research trail

WikiDrift joins ideas that already exist in the research literature (token authorship, content
survival, edit wars, cross-language comparison). Notes and paper links live in the repo’s
`sources/` folder. The contribution is the **combination**: find change from the article’s content
history, attribute the public edits, then compare wording, sources, languages, and factual claims
without presuming a conclusion in advance.
