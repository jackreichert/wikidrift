"""Findings persistence — the per-article JSON store the viewer reads (a small Repository)."""
import json
import uuid

from . import storage


def _article_slug_from_findings_name(name):
    """Return the article slug for ``Slug.kind.json`` names; shared files return None."""
    parts = str(name).split(".")
    if len(parts) >= 3:
        return parts[0]
    return None


def _write_json_atomic(path, obj):
    """Atomically overwrite *path* with JSON for *obj*."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def write_findings(name, obj):
    """Atomically overwrite a findings artifact in the canonical FINDINGS directory.

    When an article shard directory already exists under ``ARTICLES_DIR``, also mirror
    the same artifact into ``articles/<slug>/findings/`` so export (which may still
    read shards) stays in sync after analyze / L1 writes.
    """
    findings = storage.FINDINGS
    findings.mkdir(parents=True, exist_ok=True)
    _write_json_atomic(findings / name, obj)

    slug = _article_slug_from_findings_name(name)
    if not slug:
        return
    shard_dir = storage.ARTICLES_DIR / slug
    if not shard_dir.is_dir():
        return
    _write_json_atomic(shard_dir / "findings" / name, obj)


def load_findings(name, default=None):
    """Read a findings artifact, or `default` (for accumulating files like divergence/mscore)."""
    p = storage.FINDINGS / name
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            pass
    return {} if default is None else default
