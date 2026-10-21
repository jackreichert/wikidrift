#!/usr/bin/env python3
"""Exit non-zero when Topic Action update cannot proceed (hosted + missing shard).

Used by `.github/workflows/topic.yml` after R2 restore. Env:
  SHARD_DIR       — articles/<slug> directory
  CORPUS_BACKEND  — hosted | ingest
  TOPIC           — optional, for messages
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

from wikidrift.topic_action import plan_update_corpus


def main(argv: list[str] | None = None) -> int:
    del argv  # workflow invokes with no args; env carries inputs
    shard_dir = Path(os.environ.get("SHARD_DIR") or "").expanduser()
    if not shard_dir.parts:
        print("topic_shard_gate: SHARD_DIR is required", file=sys.stderr)
        return 2
    backend = os.environ.get("CORPUS_BACKEND") or "hosted"
    topic = os.environ.get("TOPIC") or shard_dir.name
    shard_path = shard_dir / "provenance.duckdb"
    present = shard_path.is_file()
    plan = plan_update_corpus(shard_present=present, corpus_backend=backend)
    prefix = f"topic={topic!r} shard={shard_path} backend={backend} kind={plan['kind']}"
    print(f"{prefix}: {plan['message']}")
    if present:
        print(f"SHARD_PRESENT=true")
    else:
        print(f"SHARD_PRESENT=false")
    print(f"NEED_INGEST={'true' if plan['need_ingest'] else 'false'}")
    return 0 if plan["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
