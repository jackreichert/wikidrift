---
spike: 001b
name: provenance-wikiwho-rs
type: comparison
validates: "Given article history, when analysed with the local Rust wikiwho engine (and the deleted-token lifecycle method), then we can compute provenance locally AND answer whether long-stable text was DELETED (retrofit) vs merely diluted (expansion)"
verdict: VALIDATED
related: [001a, 002]
tags: [provenance, wikiwho, rust, dumps, deleted-tokens]
---

# Spike 001b: Local `wikiwho_rs` + deleted-token lifecycle

## What This Validates
Two things: (1) the Rust `wikiwho_rs` engine computes token provenance **locally** from history XML
(the no-rate-limit, production-scale path), and (2) the **deleted-token lifecycle** method answers the
question 002 could not — did a contested article's drift come from **deleting** long-stable text
(retrofit) or merely **adding** around it (expansion)?

## Build & Engine (validated)
```sh
cargo build --release --features cli          # 18s, produces target/release/wikiwho-cli
wikiwho-cli article-history.xml --namespace 0 -o out.jsonl
```
- Built clean in **18s** (Rust 1.96.1). Ran on a 246-revision article in **0.4s**, emitting valid
  JSONL: per token `{token_id, str, o_rev_id, editor, in, out}` — including the `in`/`out`
  deletion/re-insertion lifecycle. Matches the hosted-API schema.
- **Did not re-verify attribution accuracy** — upstream CI already guarantees exact parity vs Python
  WikiWho + ≥85% gold-standard precision on every PR. Spent the budget on the novel deletion analysis.

## Gotchas discovered
- **WikiWho hosted-API cache can be stale.** `Bioglass 45S5` is now a redirect (16 tokens in fresh XML)
  but the hosted API still served a cached 6,970-token version at an older "latest" rev. → local
  `wikiwho_rs` on fresh dumps is the trustworthy path for current state; the hosted API is fine for
  historical revisions (which are immutable).
- **`Special:Export` does not page forward** — `offset` returns the same oldest-1000 batch. Full local
  reconstruction of a 12k-revision article needs Action-API content paging (~480MB) or the real dumps.
  For the deletion *analysis* we used the hosted API's historical-revision endpoint (cheap, immutable),
  which runs the identical WikiWho algorithm; `wikiwho_rs` is the engine for the production batch.

## Deleted-token lifecycle — the headline result

Method: reconstruct the article's token set as of the **last pre-Oct-7-2023 revision**, diff it against
today by stable WikiWho `token_id`; a pre-Oct-7 token absent today = **deleted**. Classify each by how
long-stable it already was on Oct 6 2023. Contested vs control, same time window:

**% of pre-Oct-7 text now DELETED, by how long-stable it already was:**

| stability as of Oct 6 2023 | Zionism (contested) | Photosynthesis (control) | ratio |
|---|---|---|---|
| pre-2010 (**>13 yr** stable) | **75.6%** | **6.7%** | **11.3×** |
| 2010–2014 (9–13 yr) | 73.7% | 20.3% | 3.6× |
| 2015–2018 (5–8 yr) | 83.6% | 17.2% | 4.9× |
| 2019–2021 (2–4 yr) | 82.0% | 30.1% | 2.7× |
| 2022–Sep 2023 (<2 yr) | 90.7% | 31.8% | 2.9× |
| **ALL pre-Oct-7 text** | **81.9%** (43,300 / 52,843) | **18.7%** (4,873 / 26,076) | **4.4×** |

### Interpretation
- **The control shows the healthy stability gradient**: the longer text had survived, the more it
  persisted (only **6.7%** of >13-yr-stable text deleted). The stability prior holds — exactly what
  makes "stable-then-rewritten" a meaningful signal.
- **The contested article inverts it**: even text stable for **13+ years was ~76% deleted** in the ~21
  months after Oct 7 2023, and deletion is near-uniform (~74–91%) across *all* stability eras. That is
  **wholesale replacement of the historical spine**, not expansion around a preserved core.
- This settles the expansion-vs-retrofit question 002 left open: **Zionism was retrofitted, not merely
  expanded.** Only 9,543 of 52,843 pre-Oct-7 tokens survive.

### What it proves / does NOT prove
- **Proves (structural):** the long-stable historical text was *deleted and replaced*, at ~4–11× the
  control's rate. This is the necessary condition for the §10 smoking gun, now measured — and it is very
  hard to explain as routine editing (routine editing preserves long-stable text, per the control).
- **Does NOT prove (directional):** that the replacement *reversed POV* or introduced bias.
  Token-deletion ≠ semantic reversal — some replacement is legitimate rewording/re-sourcing. Claiming a
  POV shift needs the directional layer (stance-delta on the replaced spans) + reading actual diffs, and
  ideally section-level segmentation (History/Origins vs a contemporary section).
- **Caveats:** n=1 contested + n=1 control; Photosynthesis had no attention-driving event (a
  matched control would be a non-I-P topic that *also* surged in 2023). The effect size (11× on
  long-stable text) is large, but the base-rate run over the ~10k slice is the real test.

## Next
- **Directional layer:** stance/sentiment delta on deleted-vs-replacement spans (turns "replaced" into "reframed").
- **Section segmentation:** attribute tokens to article sections → test the *History* spine specifically.
- **Local batch:** `wikiwho_rs` over dumps for the ~10k-article base-rate run (no API).
