"""Findings persistence — the per-article JSON store the viewer reads (a small Repository)."""
import json

from .storage import FINDINGS


def write_findings(name, obj):
    """Overwrite a findings artifact (per-article file) in the canonical FINDINGS dir."""
    FINDINGS.mkdir(parents=True, exist_ok=True)
    (FINDINGS / name).write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")


def load_findings(name, default=None):
    """Read a findings artifact, or `default` (for accumulating files like divergence/mscore)."""
    p = FINDINGS / name
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            pass
    return {} if default is None else default
