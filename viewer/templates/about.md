# About WikiDrift

Wikipedia was briliant idea. It was a platform for the world to collaborate together to build a single corpus of human knowledge, and it worked a little too well. Due to it's success the competition went out of buisness leaving only one place for disagreement to happen. 

It also became the source of truth for most people. Google weighs it at the top of search rankings, so every time someone searches, they get the Wikipedia version. AIs are trained off its text, so every time someone asks a question, the answer they get has been weighted towards that "truth" as well.

The nature of Wikipedia's governing body makes it easy for capture as well. Because of all this, Wikipedia has become an [attack vector to sway public discourse](https://www.bloomberg.com/news/articles/2025-03-07/wikipedia-editors-struggle-to-moderate-conflict-in-gaza) and opinion. History is getting rewritten with little recourse. [The co-founder of Wikipedia was banned from the site he created](https://www.washingtonexaminer.com/op-eds/4638304/larry-sanger-wikipedia-co-founder-banned-anonymous-mob/). In his words: "Wikipedia is ruled by a few hundred anonymous demagogues who dominate the rest."

I built WikiDrift as a forensic analysis tool to help shine a light on what is happening.

Some of the criticism against [organizations calling this out](https://www.adl.org/resources/press-release/new-adl-report-finds-evidence-biased-coordinated-campaign-wikipedia-related) has been that they themselves are partial. 

So I started with the question: Is there a way to discover bias in an article from its own history? Can an impartial analysis uncover bad actors?

WikiDrift is a result of that quesiton.

To do this the tool itself had to be impartial. It doesn't uncover bias, it *does*, however, find patterns that may or may not indicate suspicious behavior. Addionally, a lot of brilliant people have been working on this problem, so once I proved that I could discover these patterns I built into the tool that research. 

WikiDrift is an [open source](https://github.com/jackreichert/wikidrift/) research project of [encyclopediae.org](https://encyclopediae.org).

## What WikiDrift does

WikiDrift analyzes an article in layers. Not every layer runs for every article, and no layer decides whether an edit was good, bad, or biased.

| Layer or check | What it does | Research sources |
|---|---|---|
| **Foundation: Track the words** | Records where passages came from, when they disappeared, and whether they later returned. This gives every later layer a repeatable history to inspect. | [WikiWho (Flöck & Acosta, 2014)](https://doi.org/10.1145/2566486.2568026), [TokTrack (Flöck, Erdogan & Acosta, 2017)](https://arxiv.org/abs/1703.08244), and [wikiwho_rs](https://github.com/Schuwi/wikiwho_rs) |
| **Pre-check: Choose what to inspect** | Looks for unusual bursts of additions, removals, or churn in the revision metadata. These are routing leads for deeper checks, not findings about the prose. | [Detection of Editing Bursts and Extraction of Significant Keyphrases from Wikipedia Edit History (2020)](https://doi.org/10.1007/978-981-15-8731-3_4) |
| **L1: Find the rewrite** | Looks for large, lasting removal of wording that had been stable for a long time. A full analysis then narrows the strongest candidate windows to the dominant revision pair and attributes the public edits. | [Persistent Word Revisions (Halfaker et al., 2009)](https://doi.org/10.1145/1641309.1641332), [WikiTrust (Adler & de Alfaro, 2007)](https://archives.iw3c2.org/www2007/papers/paper692.pdf), [WikiWho](https://doi.org/10.1145/2566486.2568026), and [TokTrack](https://arxiv.org/abs/1703.08244) |
| **L2: Understand the change** | Uses an optional language-model check to compare the article's stance toward a focal subject across snapshots. By default, the focal subject is the article title. | [Mind Your POV (Pavalanathan, Han & Eisenstein, 2018)](https://arxiv.org/abs/1809.06951) and [Johnson et al. on Wikipedia's neutral-point-of-view practices (2025)](https://arxiv.org/abs/2510.21526) |
| **L2.5: Compare the vocabulary** | Shows how different the before-and-after vocabularies are and which terms became more or less common. It does not label those terms as good, bad, or biased. | Standard Jensen-Shannon divergence and smoothed log-odds methods; [Mind Your POV](https://arxiv.org/abs/1809.06951) is the nearest Wikipedia-specific precedent used by the project |
| **Context: Measure the edit fight** | Measures sustained mutual reverts to show how openly contested the article was. A high or low score is context, not evidence of manipulation. | [Sumi and Yasseri et al. on mutual reverts and Wikipedia conflict](https://arxiv.org/abs/1107.3689) |
| **L3: Show the evidence** | Turns an L1 candidate window into a before-and-after reading view. Where provenance is available, it also connects wording to the public revisions and accounts that introduced it. | Token-level evidence from [WikiWho](https://doi.org/10.1145/2566486.2568026) and [TokTrack](https://arxiv.org/abs/1703.08244) |
| **L4: Follow the trail** | Uses a confirmed rewrite to find other articles worth checking, then runs L1 independently on each one. Shared editors can suggest where to look, but never supply the result. | Attribution methods from [WikiWho](https://doi.org/10.1145/2566486.2568026) and [TokTrack](https://arxiv.org/abs/1703.08244); the content-first discovery sequence is WikiDrift's composition |
| **L5: Compare framing across languages** | Checks whether other-language editions frame the same subject differently. The lightweight check prefers an exact L1-confirmed revision pair, matches other editions to those timestamps, and links to every version used. It falls back to a candidate window or current leads when confirmed evidence is unavailable. | [Omnipedia (Bao et al., 2012)](https://doi.org/10.1145/2207676.2208553) and [Manypedia (Massa & Scrinzi, 2012)](https://doi.org/10.1145/2462932.2462960) |
| **L5: Compare facts across languages** | Checks a small set of important claims, such as dates, places, or counts, and reports where editions agree, differ, or contradict one another. | [InfoGap (Samir et al., 2024)](https://arxiv.org/abs/2410.04282) |
| **L5: Compare citations** | Shows how cited domains and source types differ across languages or change across a rewrite. WikiDrift reports the mix but does not rate a source as reliable or biased. | [Baigutanova et al. (2023)](https://arxiv.org/abs/2309.00196) and [Yang & Colavizza (2024)](https://doi.org/10.1108/OIR-02-2023-0084) |
| **Final reading: Put the signals together** | Places the separate checks side by side and records which thresholds fired. That count can strengthen a lead, but it is not a calibrated probability, bias score, or verdict. | [Greenstein & Zhu on Wikipedia slant](https://www.aeaweb.org/articles?id=10.1257/aer.102.3.343), their [Wikipedia-Britannica comparison](https://doi.org/10.25300/MISQ/2018/14084), and [Johnson et al.](https://arxiv.org/abs/2510.21526) |

## What's Novel?

No one publication supplies the entire pipeline. This project's contribution is the composition:
- Discover content displacement without a seed list
- Refine a coarse change window to the dominant revision pair by binary-searching the survival of long-lived wording
- Attribute the measured change through provenance
- Add semantic and external-reference checks
- Keep editor graphs downstream of content evidence

## How articles on this site were chosen

The topics here are a **curated development and regression set**, not a hit list or an independent accuracy benchmark. I picked clusters that outside sources already discuss (for example Wikipedia’s own arbitration cases, academic papers, and public reports) so I could ask: does the tool notice the same places without being fed those lists?

I also include **control** topics (like Photosynthesis) and other hot-button subjects (like Climate change) to check that ordinary big edits do not get treated as automatic proof of wrongdoing.

The software itself can run on any English Wikipedia article. This site is just the published sample.

For a deeper walkthrough of the process, see [How it works](methodology.html).

## A useful reading order

1. Open the [findings list](findings.html).
2. Pick an article and read **Overview** first.
3. Open **Rewrite** and read the removed and added text.
4. Read **Framing**, **Facts**, and **Citations** as different kinds of evidence, not interchangeable votes.
5. Follow the version links to Wikipedia when you need the original record.
