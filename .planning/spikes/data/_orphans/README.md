# Orphan article shells

Local directories moved out of `articles/` so `tools/seed_r2_shards.py` discovery
does not try to upload them.

## History_of_Zionism_(pre-1948)

- **Moved:** 2026-11-23 (Asia/Jerusalem)
- **Why:** Empty shell — `article-identity.json` + `logs/coverage.log` only; no
  `provenance.duckdb`. Seed/`cover` failed with missing DB.
- **Topics.docx / site:** list **"History of Zionism"** → slug `History_of_Zionism`
  (corpus + R2 live there).
- **Wikipedia:** `History of Zionism` currently redirects to
  `History of Zionism (pre-1948)` (pageid `14098762`). Identity resolution created
  this slug; the ingest/corpus stayed under `History_of_Zionism`.
- **Action:** Do **not** rematerialize unless Jack explicitly wants a separate
  pre-1948 slug. Keep using `History_of_Zionism`.
