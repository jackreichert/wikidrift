---
spike: 001a
name: provenance-wikiwho-api
type: comparison
validates: "Given a real enwiki article, when queried via the hosted WikiWho API + MediaWiki Action API, then we get per-token {editor, origin_revision, origin_time, in/out churn} — the §10 drift inputs — with no Rust and no full-history dump"
verdict: VALIDATED
related: [001b]
tags: [provenance, wikiwho, api, duckdb]
---

# Spike 001a: Provenance via hosted WikiWho API

## What This Validates
Given a real enwiki article, when queried via the hosted WikiWho API (`rev_content`) plus the
MediaWiki Action API (for the `rev_id → timestamp/user` timeline), then we obtain per-token
`{editor, origin_revision, origin_time, in/out churn}` — the inputs the §10 drift engine needs —
without a Rust build or the 31 TB full-history dump.

## Research
- **WikiWho API** live at `https://wikiwho.wmcloud.org/en/api/v1.0.0-beta/` (English confirmed).
  - `rev_content/{title}/?editor=true&token_id=true&o_rev_id=true&out=true&in=true` →
    latest revision's **surviving** tokens, each with `str`, `o_rev_id` (origin revision),
    `editor` (origin editor; anon encoded as `0|IP`), stable `token_id`, and `in`/`out`
    (revisions where the token was re-inserted / deleted → revert markers).
  - `rev_ids/{title}/` → all revision ids, **but no timestamps** → not enough on its own.
- **MediaWiki Action API** supplies the timeline: `prop=revisions&rvprop=ids|timestamp|user&rvlimit=max&rvdir=newer`, serial + `maxlag=5` + `rvcontinue` pagination.
- **Gotcha:** hosted API returns *surviving* tokens only — deleted tokens (needed for the pure
  "long-stable-then-deleted" signal) are not enumerated here → that's spike 001b's job.

**Chosen approach:** WikiWho `rev_content` for token provenance + Action API for the dated timeline; join on `o_rev_id = rev_id` in DuckDB.

## How to Run
```bash
uv run python fetch_provenance.py "Photosynthesis" "Zionism"
# writes ../data/provenance.duckdb (tables: articles, revisions, tokens)
```

## What to Expect
Per article: token count, revision-timeline size, a join report (tokens → dated origin revision), and sample rows. Data persisted to the shared DuckDB for spike 002.

## Investigation Trail
1. Probed the API on a redirect (`Bioglass`) → learned the `revisions[0][rev_id].tokens[]` shape.
2. Probed `Bioglass_45S5` with `o_rev_id/in/out` → confirmed per-token origin rev + churn lists.
3. Found `rev_ids` lacks timestamps → added Action-API timeline fetch + DuckDB join.
4. Ran small article (6,970 tokens, 246 revs, 5s) → 100% join. Then the real pair.

## Results
**VALIDATED.** Complete per-token provenance at production scale, fast, no Rust/dump:

| Article | Surviving tokens | Revisions (Action-API calls) | Join rate | Time |
|---|---|---|---|---|
| Bioglass_45S5 | 6,970 | 246 (1) | 6,970/6,970 (100%) | 5.0s |
| Photosynthesis | 27,158 | 5,503 (12) | 27,158/27,158 (100%) | 23.5s |
| Zionism | 88,667 | 12,483 (25) | 88,667/88,667 (100%) | 71.1s |

- **100% of tokens map to a dated origin revision** in all three — the join is clean.
- Anon editors surface as `0|IP`; registered editors as numeric ids (fine for concentration).
- Zionism (12.4k revs) fully pulled in 25 serial Action-API calls with `maxlag` — well within etiquette; a ~10k-article subset is clearly feasible on a laptop.

**Surprise / limitation:** only surviving tokens are returned, so the churn fields describe the
*current* text's history, not deleted text. Confirmed downstream (002) that this limits the pure
smoking-gun metric → motivates **001b (`wikiwho_rs`)** for deleted-token lifecycles.
