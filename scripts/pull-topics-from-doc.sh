#!/usr/bin/env bash
# Pull / update local history for titles from WikiDrift Neutral About and Topics.docx.
#
# Safe path (see docs/safe-history-updates.md + docs/local-topic-data-download.md):
#   1. If local shard already has ≥3 snapshots → leave corpus alone.
#   2. Else prefer R2 shard pull (atomic decompress) when remote shard exists.
#   3. Else `wikidrift ingest` WITHOUT --force (skips extract when ≥3 snaps).
#   4. Then `cover_missing_topics.py --mode fill` (resume; skip-if-complete).
#
# Usage (from repo root):
#   ./scripts/pull-topics-from-doc.sh              # execute
#   ./scripts/pull-topics-from-doc.sh --dry-run    # print actions only
#   WIKIDRIFT_ROOT=/path/to/wikidrift ./scripts/pull-topics-from-doc.sh
#
# Does not print .env secrets. Never uses ingest --force or cover --mode full.

set -euo pipefail

DRY_RUN=0
if [[ "${1:-}" == "--dry-run" ]]; then
  DRY_RUN=1
  shift
fi

# Prefer script-relative checkout root so this works wherever the repo lives.
ROOT="${WIKIDRIFT_ROOT:-$(cd "$(dirname "$0")/.." && pwd)}"
ARTICLES_ROOT="$ROOT/.planning/spikes/data/articles"
STAGE="${TMPDIR:-/tmp}/wikidrift-r2-pull-topics-from-doc"
MIN_SNAPS=3

TITLES=(
  "Zionism"
  "Anti-Zionism"
  "History of Zionism"
  "Racial conceptions of Jewish identity in Zionism"
  "Genetic studies of Jews"
  "Israeli–Palestinian conflict"
  "Palestine"
  "Nakba"
  "Gaza war"
  "Gaza genocide"
  "Rafah offensive"
  "Hamas"
  "Palestinian political violence"
  "UNRWA"
  "United Nations General Assembly observers"
  "Bar Kokhba Revolt"
  "Jedwabne pogrom"
  "Collaboration in German-occupied Poland"
  "Rescue of Jews by Poles during the Holocaust"
  "Naliboki massacre"
  "Warsaw concentration camp"
  "Democratic Party (United States)"
  "Republican Party (United States)"
  "Democratic Socialists of America"
  "Elizabeth Warren"
  "Ilhan Omar"
  "Xi Jinping"
  "Capitalism"
  "Socialism"
  "Fascism and ideology"
  "Abortion"
  "Climate change"
  "Genocide of indigenous peoples"
  "Ainu people"
  "Photosynthesis"
  "Chess"
  "Brontosaurus"
  "Water"
  "Ted Cruz"
  "Marjorie Taylor Greene"
  "Boris Johnson"
  "Jeremy Corbyn"
  "Narendra Modi"
  "Rahul Gandhi"
  "Jair Bolsonaro"
  "Luiz Inácio Lula da Silva"
  "Russo-Ukrainian war (2022–present)"
  "Kashmir conflict"
  "Nagorno-Karabakh conflict"
  "The Troubles"
  "Western Sahara conflict"
  "Catholic Church"
  "Islam"
  "Scientology"
  "Falun Gong"
  "Tesla, Inc."
  "ExxonMobil"
  "Purdue Pharma"
  "COVID-19 lab leak theory"
  "Genetically modified food controversies"
  "Nuclear power debate"
  "Christopher Columbus"
  "Winston Churchill"
  "Che Guevara"
  "Holodomor"
  "Mitochondria"
  "Jupiter"
  "Violin"
  "Pythagorean theorem"
)

die() { echo "error: $*" >&2; exit 1; }

slugify() {
  # Mirrors wikidrift.config.parsing.slugify: spaces / path seps → underscore; Unicode kept.
  local t="$1"
  t="${t// /_}"
  t="${t//\//_}"
  t="${t//\\/_}"
  printf '%s' "$t"
}

run() {
  if [[ "$DRY_RUN" -eq 1 ]]; then
    printf '[dry-run]'
    printf ' %q' "$@"
    printf '\n'
    return 0
  fi
  "$@"
}

need_cmd() {
  command -v "$1" >/dev/null 2>&1 || die "missing required command: $1"
}

snap_count() {
  local title="$1" db="$2"
  [[ -f "$db" ]] || { echo 0; return; }
  # Best-effort; locked / corrupt DBs report 0 so we can still try R2/ingest.
  # Matches Corpus.snapshot_count usable-snap semantics when snapshot_integrity exists.
  uv run python - "$title" "$db" <<'PY' 2>/dev/null || echo 0
import sys
import duckdb
title, db = sys.argv[1], sys.argv[2]
try:
    con = duckdb.connect(db, read_only=True)
    has = con.execute(
        "SELECT count(*) FROM information_schema.tables "
        "WHERE table_schema='main' AND table_name='snapshot_integrity'"
    ).fetchone()[0]
    if has:
        n = con.execute(
            "SELECT count(DISTINCT snap_rev) FROM rsnap r WHERE article=? "
            "AND NOT EXISTS ("
            "  SELECT 1 FROM snapshot_integrity i "
            "  WHERE i.article=r.article AND i.snap_rev=r.snap_rev "
            "  AND i.status='quarantined')",
            [title],
        ).fetchone()[0]
    else:
        n = con.execute(
            "SELECT count(DISTINCT snap_rev) FROM rsnap WHERE article=?",
            [title],
        ).fetchone()[0]
    print(int(n))
except Exception:
    print(0)
PY
}

load_dotenv() {
  local env_file="$ROOT/.env"
  [[ -f "$env_file" ]] || return 0
  set -a
  # shellcheck disable=SC1090
  source "$env_file"
  set +a
}

r2_ready() {
  [[ -n "${R2_ACCOUNT_ID:-}" && -n "${R2_ACCESS_KEY_ID:-}" \
    && -n "${R2_SECRET_ACCESS_KEY:-}" && -n "${R2_BUCKET:-}" ]]
}

r2_setup() {
  PREFIX="${R2_PREFIX:-}"
  case "$PREFIX" in ""|*/) ;; *) PREFIX="${PREFIX}/" ;; esac
  ENDPOINT="https://${R2_ACCOUNT_ID}.r2.cloudflarestorage.com"
  export AWS_ACCESS_KEY_ID="$R2_ACCESS_KEY_ID"
  export AWS_SECRET_ACCESS_KEY="$R2_SECRET_ACCESS_KEY"
  export AWS_DEFAULT_REGION="${AWS_DEFAULT_REGION:-auto}"
  unset AWS_PROFILE || true
}

r2_has_shard() {
  local slug="$1"
  aws s3 ls "s3://${R2_BUCKET}/${PREFIX}articles/${slug}/shard.duckdb.zst" \
    --endpoint-url "$ENDPOINT" >/dev/null 2>&1
}

pull_r2_shard() {
  local slug="$1"
  local dest="$ARTICLES_ROOT/$slug"
  local tmp_dir="$STAGE/$slug"
  local tmp_zst="$tmp_dir/shard.duckdb.zst"
  local tmp_db="$tmp_dir/provenance.duckdb.tmp"
  local remote="s3://${R2_BUCKET}/${PREFIX}articles/${slug}"

  mkdir -p "$dest" "$tmp_dir"
  echo "  R2: downloading shard for $slug"
  if [[ "$DRY_RUN" -eq 1 ]]; then
    echo "  [dry-run] aws s3 cp ${remote}/shard.duckdb.zst → stage; zstd → atomic mv $dest/provenance.duckdb"
  else
    aws s3 cp "${remote}/shard.duckdb.zst" "$tmp_zst" --endpoint-url "$ENDPOINT"
    echo "  R2: decompress (temp) → atomic mv"
    zstd -d -f -o "$tmp_db" "$tmp_zst"
    mv "$tmp_db" "$dest/provenance.duckdb"
    if aws s3 ls "${remote}/meta.json" --endpoint-url "$ENDPOINT" >/dev/null 2>&1; then
      aws s3 cp "${remote}/meta.json" "$dest/r2-meta.json" --endpoint-url "$ENDPOINT" || true
    fi
  fi
  echo "  R2: restored $slug"
}

ingest_title() {
  local title="$1" slug="$2"
  local data_dir="$ARTICLES_ROOT/$slug"
  mkdir -p "$data_dir"
  echo "  ingest (no --force): $title"
  if [[ "$DRY_RUN" -eq 1 ]]; then
    echo "  [dry-run] WIKIDRIFT_DATA_DIR=$data_dir uv run wikidrift ingest \"$title\""
  else
    WIKIDRIFT_DATA_DIR="$data_dir" uv run wikidrift ingest "$title"
  fi
}

[[ -d "$ROOT" ]] || die "checkout not found: $ROOT (set WIKIDRIFT_ROOT)"
[[ -f "$ROOT/pyproject.toml" ]] || die "not a wikidrift checkout: $ROOT"
need_cmd uv
cd "$ROOT"
load_dotenv
mkdir -p "$ARTICLES_ROOT" "$STAGE"

R2_OK=0
if r2_ready; then
  need_cmd aws
  need_cmd zstd
  r2_setup
  R2_OK=1
  echo "R2 credentials loaded (bucket present; values not printed)."
else
  echo "R2 env incomplete — will skip shard pull and use ingest for missing titles."
fi

echo "Checkout: $ROOT"
echo "Articles: $ARTICLES_ROOT"
echo "Titles:   ${#TITLES[@]}"
[[ "$DRY_RUN" -eq 1 ]] && echo "Mode:     dry-run"
echo

skipped=0
pulled=0
ingested=0
failed=0
declare -a COVER_TITLES=()

for title in "${TITLES[@]}"; do
  slug="$(slugify "$title")"
  db="$ARTICLES_ROOT/$slug/provenance.duckdb"
  echo "==> $title  (slug=$slug)"

  n="$(snap_count "$title" "$db")"
  if [[ "$n" -ge "$MIN_SNAPS" ]]; then
    echo "  local OK ($n snaps ≥ $MIN_SNAPS) — skip corpus pull/ingest"
    skipped=$((skipped + 1))
    COVER_TITLES+=("$title")
    continue
  fi

  got_corpus=0
  if [[ "$R2_OK" -eq 1 ]]; then
    if r2_has_shard "$slug"; then
      if pull_r2_shard "$slug"; then
        pulled=$((pulled + 1))
        got_corpus=1
        if [[ "$DRY_RUN" -eq 1 ]]; then
          echo "  [dry-run] R2 shard present — skip ingest (live run rechecks snap count)"
        else
          n="$(snap_count "$title" "$db")"
          echo "  after R2: $n snaps"
        fi
      else
        echo "  R2 pull failed for $slug — will try ingest" >&2
      fi
    else
      echo "  no remote shard at articles/${slug}/shard.duckdb.zst"
    fi
  fi

  need_ingest=0
  if [[ "$got_corpus" -eq 0 ]]; then
    need_ingest=1
  elif [[ "$DRY_RUN" -eq 0 && "${n:-0}" -lt "$MIN_SNAPS" ]]; then
    # R2 restored but still thin — fill without --force.
    need_ingest=1
  fi

  if [[ "$need_ingest" -eq 1 ]]; then
    if ingest_title "$title" "$slug"; then
      ingested=$((ingested + 1))
      if [[ "$DRY_RUN" -eq 0 ]]; then
        n="$(snap_count "$title" "$db")"
        echo "  after ingest: $n snaps"
      fi
    else
      echo "  ingest failed for $title" >&2
      failed=$((failed + 1))
      continue
    fi
  fi

  COVER_TITLES+=("$title")
done

echo
echo "=== cover fill (resume; skip-if-complete) ==="
if [[ "${#COVER_TITLES[@]}" -eq 0 ]]; then
  echo "No titles eligible for cover."
else
  echo "Topics: ${#COVER_TITLES[@]}"
  if [[ "$DRY_RUN" -eq 1 ]]; then
    printf '[dry-run] uv run python tools/cover_missing_topics.py --mode fill --execute --topics'
    printf ' %q' "${COVER_TITLES[@]}"
    printf '\n'
  else
    uv run python tools/cover_missing_topics.py --mode fill --execute --topics "${COVER_TITLES[@]}"
  fi
fi

echo
echo "Done. skipped_ok=$skipped r2_pulled=$pulled ingested=$ingested failed=$failed cover_topics=${#COVER_TITLES[@]}"
[[ "$failed" -eq 0 ]] || exit 1
