#!/usr/bin/env python3
"""Convenience launcher for tools/cover_missing_topics.py.

Lets you run either:
  uv run python tools/cover_missing_topics.py ...
or:
  uv run python cover_missing_topics.py ...
"""

from tools.cover_missing_topics import main


if __name__ == "__main__":
    raise SystemExit(main())
