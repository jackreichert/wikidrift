# Ops: Topic update / build (`topic-ops`)

Operator checklist for [`.github/workflows/topic.yml`](.github/workflows/topic.yml).
Tickets: **wikidrift-007** (ops checklist), **wikidrift-011** (cold-start Action path), **wikidrift-013** (Actions RAM-safe ingest).
Design note (Project store): `docs/topic-action.md` / `docs/topic-action-coldstart.md`.

## 1. GitHub Environment secrets

Create Environment **`topic-ops`** (*Settings → Environments*). The workflow job binds to that Environment — secrets live there, not in repo variables.

| Secret | Required? | Role |
| --- | --- | --- |
| `XAI_API_KEY` | Yes for default path | LLM layers (`provider=xai`) |
| `R2_ACCOUNT_ID` | Yes for R2 restore/upload | Hex account id in the S3 endpoint host |
| `R2_ACCESS_KEY_ID` | Yes for R2 | R2 API token access key |
| `R2_SECRET_ACCESS_KEY` | Yes for R2 | R2 API token secret |
| `R2_BUCKET` | Yes for R2 | Bucket name |
| `R2_PREFIX` | Optional | Key prefix (e.g. `prod/`); default empty |
| `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` / `GOOGLE_API_KEY` | Optional | Alternate LLM providers |
| `WIKIDRIFT_LLM_API_KEY` | Optional | Provider-agnostic override |

Incomplete R2 secrets soft-skip restore/upload. Missing LLM keys fail layers that need them. Without a local/R2 shard, cold-start requires `corpus_backend=ingest` (hosted alone is blocked).

### Account ID and token IP filter

- **`R2_ACCOUNT_ID`** is the hex id in `https://<id>.r2.cloudflarestorage.com` (Cloudflare dashboard → R2 → account endpoint). It is not an API token.
- When creating the R2 API token, **do not set an IP allowlist**. GitHub Actions runners have dynamic egress IPs; an IP filter will break restore/upload from Actions.

## 2. R2 key layout

Per article only — **never** sync `history-xml/` or a shared monolithic `provenance.duckdb`:

```text
{R2_PREFIX}articles/<slug>/shard.duckdb.zst
{R2_PREFIX}articles/<slug>/meta.json
```

- Restore: download `.zst` → decompress to `.planning/spikes/data/articles/<slug>/provenance.duckdb`
- Upload: zstd compress → stage under `articles/<slug>/_upload_<run_id>/` → promote to final keys → delete staging
- `meta.json`: title, slug, last_revid, horizon, updated_at, source (no secrets)

## 3. Run the Action

Actions tab → **Topic update / build** → **Run workflow**.

| Input | Values | Notes |
| --- | --- | --- |
| `topic` | Wikipedia title **or** enwiki URL | Required (e.g. `Zionism` or `https://en.wikipedia.org/wiki/Zionism`) |
| `mode` | `update` \| `build` | `build` = site only (no R2, no reanalyze) |
| `corpus_backend` | `hosted` \| `ingest` | `update` only; see §4 |
| `provider` / `model` | e.g. `xai` / `grok-4.3` | Defaults match repo LLM config |
| `framing` | bool | L5 Framing Lite on `update` (default on) |
| `commit` | bool | Commit findings + `docs/` (+ `viewer/data/`) (default on) |

`topic` accepts a bare title or a Wikipedia URL (`/wiki/…` or `/w/index.php?title=…`). The Action normalizes URLs to a title, then follows MediaWiki redirects to the **canonical** title before slugify / R2 keys / ingest / cover (same resolve as `provenance.resolve_article_title`). Redirects change the slug — e.g. `Iran War` → `List of wars involving Iran` → `List_of_wars_involving_Iran`. Prefer the canonical title as input, or expect the Action to rewrite `TOPIC`/`SLUG` when a redirect is logged (`resolved redirect: … -> …`).

## 4. Brand-new topic (cold-start) vs update (delta)

Actions runners have **no** Mac parent corpus. A title that is not already under `articles/<slug>/` in R2 must be **ingested** on the runner.

### First run — brand-new Wikipedia title (not in R2)

| Input | Value |
| --- | --- |
| `topic` | Exact Wikipedia article title **or** enwiki URL |
| `mode` | `update` |
| `corpus_backend` | **`ingest`** (required) |
| `provider` / `model` | `xai` / `grok-4.3` (or your keys) |
| `framing` | `true` (typical) |
| `commit` | `true` (typical) |

What happens:

1. Normalize `topic` (URL → title → MediaWiki canonical) and resolve the article slug.
2. R2 restore soft-skips (no `shard.duckdb.zst` for the slug).
3. Gate allows proceed only because `corpus_backend=ingest`.
4. Build `tools/snapshot-tokens` → `wikidrift ingest "<topic>"` under `articles/<slug>/`.
5. `cover_missing_topics --mode full` (analyze + pipeline layers).
6. Upload `shard.duckdb.zst` + `meta.json` → seeds R2 for later deltas.
7. Site build; optional commit of findings/`docs/`.

Needs MediaWiki Action API (history XML). Does **not** need hosted WikiWho. Needs LLM keys for layers that call an LLM.

### Hosted without a shard — blocked

`mode=update` + `corpus_backend=hosted` + no local/R2 `provenance.duckdb` **fails** with a clear error:

> seed with `corpus_backend=ingest`, or seed R2 from a Mac first

Do not use `hosted` for a title that has never been seeded.

### Later runs — delta (slug already in R2)

| Situation | Inputs |
| --- | --- |
| Normal incremental | `mode=update`, `corpus_backend=hosted` |
| WikiWho down / coverage gap | `mode=update`, `corpus_backend=ingest` |

Restore decompresses the prior shard; cover/ingest run **without `--force`** so existing revision snapshots are skipped; upload replaces the slug keys.

`mode=build` never touches R2 (site from committed findings only).

| Situation | Behavior |
| --- | --- |
| **Cold start** | No R2 shard → must use `ingest`; first successful upload seeds R2 |
| **Delta** | R2 restore → hosted (default) or ingest → incremental cover → upload |
| **Hosted + missing shard** | Gate fails; re-run with `ingest` |

### Large-article cold-start on Actions (`ubuntu-latest` ~7GB)

Free runners OOM when `snapshot-tokens` loads a multi‑GB full-history dump and Python buffers every snap’s token lines at once (seen on **2026 Iran war**: XML OK → runner killed during extract). Ingest is now memory-bounded:

- Incomplete XML caches (killed mid-write, no `</mediawiki>`) are discarded and re-fetched.
- `snapshot-tokens` drops each revision’s dump text after analysis, truncates after the newest requested pick, and treats an empty target list as a no-op (never “all”).
- Python streams helper stdout and stages `rsnap` in pick batches (`WIKIDRIFT_INGEST_SNAP_BATCH`, default **1**) so the full token universe is never held in RAM.

**Retry Iran war (or similar) after merge:** Actions → Topic update / build → `topic=https://en.wikipedia.org/wiki/2026_Iran_war` (or canonical title), `mode=update`, `corpus_backend=ingest`. Prefer the canonical title if a redirect rewrites the slug. One workflow_dispatch is enough — no multi-job split.

**Still Mac-first when:** the history XML alone is multi‑GB *and* Actions still dies after this path (peak is still “wikiwho graph up through the latest pick”). Seed with `uv run wikidrift ingest "…"` locally, then `tools/seed_r2_shards.py --topics "…"`, then Actions `hosted` deltas.

## 5. Optional Mac seed (existing corpus → R2)

Use when the article already lives in a local canonical corpus and you want to seed R2 without a full Actions ingest.

Requires local `.env` with `R2_ACCOUNT_ID`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`, `R2_BUCKET` (optional `R2_PREFIX`), plus `zstd` and the AWS CLI on `PATH`. Never uploads `history-xml/` or the shared monolithic `provenance.duckdb`.

After the corpus pull / migrate finishes:

```bash
cd ~/dev-env/wikidrift && git pull
uv run python tools/seed_r2_shards.py --dry-run
uv run python tools/seed_r2_shards.py
```

Useful flags:

| Flag | Behavior |
| --- | --- |
| `--dry-run` | List what would upload (no compress / R2 writes) |
| `--force` | Re-upload even when remote `meta.json` + shard look up-to-date |
| `--topics A B` | Only these Wikipedia titles (slugified) |
| `--all` | Every article dir under `.planning/spikes/data/articles/` (same as default when `--topics` omitted) |

Default skip heuristic: if both remote keys exist and remote LastModified is ≥ local `provenance.duckdb` mtime, the slug is skipped. Locked DuckDB files (e.g. an in-progress pull) are skipped with a clear message; the loop continues. Missing shard DBs are rematerialized from the parent canonical `provenance.duckdb` via `shards.ensure_article_database` when the article exists there.

One-off / first-time setup path (if shards are not already present):

1. Bootstrap / populate `.planning/spikes/data/` (e.g. `uv run wikidrift bootstrap "<Title>"`).
2. `uv run wikidrift migrate-shards` → `articles/<slug>/provenance.duckdb`.
3. Put Environment secrets on `topic-ops` (section 1), R2 with **no IP filter**.
4. Run `tools/seed_r2_shards.py` as above (or a single title with `--topics`).
5. Later Actions runs are **delta**: `mode=update` + `corpus_backend=hosted`.

For a title **not** in the Mac corpus, skip this section — use §4 cold-start (`corpus_backend=ingest`) only.

## 6. Quick verify

- Environment `topic-ops` lists the secrets above.
- Cloudflare token: Object Read & Write on the bucket; **Client IP Address Filtering** off.
- Smoke: `mode=build` for a known topic.
- New title: `mode=update` + `corpus_backend=ingest` once (title or URL); confirm R2 keys under `articles/<slug>/`.
- Next run: `mode=update` + `corpus_backend=hosted` for the same title.
