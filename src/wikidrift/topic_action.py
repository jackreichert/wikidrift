"""Policy helpers for the Topic update / build GitHub Action (`.github/workflows/topic.yml`).

Keeps cold-start vs delta decisions out of shell so they stay unit-tested and documented in one place.
"""
from __future__ import annotations


def plan_update_corpus(*, shard_present: bool, corpus_backend: str) -> dict:
    """Decide how an Action ``update`` should proceed given shard presence and backend.

    Returns a dict with:
      - ``ok``: whether the workflow may continue
      - ``kind``: ``delta`` | ``cold_start`` | ``cold_start_blocked``
      - ``need_ingest``: whether the ingest step must run
      - ``message``: operator-facing notice or error text
    """
    backend = (corpus_backend or "hosted").strip().lower()
    if backend not in {"hosted", "ingest"}:
        raise ValueError(f"unknown corpus_backend: {corpus_backend!r} (expected hosted|ingest)")

    if shard_present:
        return {
            "ok": True,
            "kind": "delta",
            "need_ingest": backend == "ingest",
            "message": (
                f"Article shard present; incremental update (corpus_backend={backend})."
            ),
        }

    # No local provenance.duckdb after R2 restore attempt → true cold-start.
    if backend == "ingest":
        return {
            "ok": True,
            "kind": "cold_start",
            "need_ingest": True,
            "message": (
                "No article shard for this slug (R2 miss or empty). "
                "Cold-start via corpus_backend=ingest: MediaWiki history XML + "
                "snapshot-tokens → article shard, then cover/pipeline."
            ),
        }

    return {
        "ok": False,
        "kind": "cold_start_blocked",
        "need_ingest": True,
        "message": (
            "No article shard for this slug in R2/local "
            "(.planning/spikes/data/articles/<slug>/provenance.duckdb). "
            "Hosted WikiWho updates require a seeded shard. "
            "Re-run with corpus_backend=ingest for a true cold-start, "
            "or seed R2 from a Mac (bootstrap / migrate-shards + upload) first."
        ),
    }
