"""Policy helpers for the Topic update / build GitHub Action (`.github/workflows/topic.yml`).

Keeps cold-start vs delta decisions out of shell so they stay unit-tested and documented in one place.
"""
from __future__ import annotations

from . import config, provenance


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


def _title_key(title: str) -> str:
    """Compare titles ignoring underscore/space (URL vs display form)."""
    return (title or "").strip().replace("_", " ")


def resolve_topic_for_action(
    raw_topic: str,
    *,
    resolver=None,
    normalize=config.normalize_article_arg,
    slugify=config.slugify,
) -> dict:
    """URL-normalize then MediaWiki-canonicalize before slugify / shard keys.

    Same MediaWiki redirect follow as ``provenance.resolve_article_title``, so ingest,
    R2 keys, SHARD_DIR, and cover_missing_topics all agree on one slug.

    Returns:
      requested_title, canonical_title, slug, page_id, redirected
    """
    requested = normalize(raw_topic)
    if not requested:
        raise ValueError("topic input normalized to empty string")
    resolve = resolver or provenance.resolve_article_title
    resolved = resolve(requested)
    canonical = resolved.canonical_title
    if not (canonical or "").strip():
        raise ValueError(f"MediaWiki resolved empty canonical title for {requested!r}")
    redirected = _title_key(requested) != _title_key(canonical)
    return {
        "requested_title": requested,
        "canonical_title": canonical,
        "slug": slugify(canonical),
        "page_id": int(resolved.page_id),
        "redirected": redirected,
    }
