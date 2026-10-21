# Ops: Topic update / build (`topic-ops`)

Operator checklist for [`.github/workflows/topic.yml`](.github/workflows/topic.yml).
Ticket: **wikidrift-007**. Design note (Project store): `docs/topic-action.md`.

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

Incomplete R2 secrets soft-skip restore/upload (cold start still runs analyze). Missing LLM keys fail layers that need them.

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
| `topic` | Wikipedia title | Required (e.g. `Zionism`) |
| `mode` | `update` \| `build` | `build` = site only (no R2, no reanalyze) |
| `corpus_backend` | `hosted` \| `ingest` | `update` only; default `hosted` |
| `provider` / `model` | e.g. `xai` / `grok-4.3` | Defaults match repo LLM config |
| `framing` | bool | L5 Framing Lite on `update` (default on) |
| `commit` | bool | Commit findings + `docs/` (+ `viewer/data/`) (default on) |

`corpus_backend=ingest` builds `tools/snapshot-tokens` and runs `wikidrift ingest` under the article shard when hosted WikiWho is down or won't cover. Re-run with `ingest` after a hosted failure.

## 4. Cold start vs delta

| Situation | Behavior |
| --- | --- |
| **Cold start** | No `shard.duckdb.zst` for the slug (or R2 secrets incomplete). Restore soft-skips; analyze fills the local shard; first successful upload seeds R2. |
| **Delta (after seed)** | Restore decompresses the prior shard; cover/ingest run **without `--force`** so existing revision snapshots are skipped; only new work is fetched; upload replaces the slug keys. |

`mode=build` never touches R2.

## 5. First-run seed from a Mac (high level)

Full automated seed script can wait until the local corpus download finishes. Until then, operators can seed one slug manually after bootstrap + shard migration:

1. **Bootstrap** (or otherwise populate) the canonical corpus under `.planning/spikes/data/` — e.g. `uv run wikidrift bootstrap "<Title>"` or an in-progress download of an existing corpus.
2. **Migrate shards:** `uv run wikidrift migrate-shards` → article-owned DuckDB under `.planning/spikes/data/articles/<slug>/provenance.duckdb` (canonical DB left intact).
3. **Confirm** `articles/<slug>/provenance.duckdb` exists for the topic you care about.
4. **Put Environment secrets** on `topic-ops` (section 1), including R2 with **no IP filter**.
5. **Seed R2 for that slug** — either:
   - run Actions → **Topic update / build** with `mode=update` (cold start uploads after analyze), or
   - after download finishes, use a dedicated seed script / `aws s3 cp` of `shard.duckdb.zst` + `meta.json` to the layout above (script TBD on **wikidrift-007**).
6. Later runs are **delta**: restore → incremental cover → upload.

Do not upload `history-xml/` or the shared `provenance.duckdb`.

## 6. Quick verify

- Environment `topic-ops` lists the secrets above.
- Cloudflare token: Object Read & Write on the bucket; **Client IP Address Filtering** off.
- Smoke: `mode=build` for a known topic, then `mode=update` once for a seeded slug and confirm R2 keys appear under `articles/<slug>/`.
