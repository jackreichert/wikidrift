"""Findings persistence — the per-article JSON store the viewer reads (a small Repository)."""
import json
import uuid

from .storage import FINDINGS


def write_findings(name, obj):
    """Atomically overwrite a findings artifact in the canonical FINDINGS directory."""
    FINDINGS.mkdir(parents=True, exist_ok=True)
    output = FINDINGS / name
    temporary = output.with_name(f".{output.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(output)
    finally:
        temporary.unlink(missing_ok=True)


def load_findings(name, default=None):
    """Read a findings artifact, or `default` (for accumulating files like divergence/mscore)."""
    p = FINDINGS / name
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            pass
    return {} if default is None else default
