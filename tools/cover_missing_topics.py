#!/usr/bin/env python3
"""Run topic coverage workflows in one command.

This helper inspects `.planning/spikes/data/findings` and can either:
- run a full workflow for each selected topic (`--mode full`), or
- only fill missing layers (`--mode fill`).

Selection:
- pass topics explicitly with `--topics`,
- or from a newline file with `--topics-file`,
- or let the script auto-discover partial topics from findings,
- or restrict to controls via `--only-controls`.

Safety:
- Focal selection is controversy-agnostic for out-of-slate topics, so --llm can run safely on
    full topic lists without topic-specific overrides.

Run examples:
    uv run python tools/cover_missing_topics.py --topics "Chess" "Water" --mode full --execute
    uv run python tools/cover_missing_topics.py --topics "Brontosaurus" "Abortion" --mode fill --execute
    uv run python tools/cover_missing_topics.py --only-controls --mode fill --execute
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

DEFAULT_REQUIRED_LAYERS = ["receipts", "stance", "factcheck", "sources", "profile"]
DEFAULT_CONTROLS = [
    "Photosynthesis",
    "Brontosaurus",
    "Climate change",
    "Abortion",
    "Chess",
    "Water",
]


def _title_from_filename(path: Path, suffix: str) -> str:
    return path.name.removesuffix(suffix).replace("_", " ")


def _load_topic_layers(findings_dir: Path) -> dict[str, set[str]]:
    layers: dict[str, set[str]] = {}

    def add(topic: str, layer: str) -> None:
        if not topic:
            return
        layers.setdefault(topic, set()).add(layer)

    for p in findings_dir.glob("*.receipts.json"):
        add(_title_from_filename(p, ".receipts.json"), "receipts")
    for p in findings_dir.glob("*.stance.json"):
        add(_title_from_filename(p, ".stance.json"), "stance")
    for p in findings_dir.glob("*.sources.json"):
        add(_title_from_filename(p, ".sources.json"), "sources")
    for p in findings_dir.glob("*.profile.json"):
        add(_title_from_filename(p, ".profile.json"), "profile")

    for p in findings_dir.glob("*.factcheck.json"):
        try:
            payload = json.loads(p.read_text(encoding="utf-8"))
            add((payload.get("article") or "").strip(), "factcheck")
        except Exception:
            continue

    try:
        divergence = json.loads((findings_dir / "divergence.json").read_text(encoding="utf-8"))
    except Exception:
        divergence = {}
    for topic in (divergence.get("static") or {}).keys():
        add(str(topic), "divergence_static")
    for topic in (divergence.get("pivot_relative") or {}).keys():
        add(str(topic), "divergence_pivot_relative")

    return layers


def _run(cmd: list[str], execute: bool) -> int:
    rendered = " ".join(subprocess.list2cmdline([part]) for part in cmd)
    print(f"  $ {rendered}")
    if not execute:
        return 0
    completed = subprocess.run(cmd)
    return int(completed.returncode)


def _pipeline_cmd(topic: str, use_llm: bool, include_mscore: bool) -> list[str]:
    base = [sys.executable, "-m", "wikidrift.cli"]
    cmd = base + ["pipeline", topic]
    if use_llm:
        cmd.append("--llm")
    if include_mscore:
        cmd.append("--mscore")
    return cmd


def _topic_commands(topic: str, use_llm: bool, include_mscore: bool, mode: str,
                    required: set[str], have: set[str]) -> tuple[list[list[str]], list[str]]:
    """Return (commands, notes) for this topic under the selected mode."""
    base = [sys.executable, "-m", "wikidrift.cli"]
    cmds: list[list[str]] = []
    notes: list[str] = []

    if mode == "full":
        # Full path always runs pipeline once.
        pipeline_llm = use_llm
        cmds.append(_pipeline_cmd(topic, use_llm=pipeline_llm, include_mscore=include_mscore))
        cmds.append(base + ["sources", topic])
        cmds.append(base + ["profile", topic])
        return cmds, notes

    # mode == fill: run the smallest command set that can cover missing layers.
    missing = required - have
    needs_core = bool({"receipts", "stance", "factcheck"} & missing)
    if needs_core:
        pipeline_llm = use_llm
        cmds.append(_pipeline_cmd(topic, use_llm=pipeline_llm, include_mscore=include_mscore))

    if "sources" in missing:
        cmds.append(base + ["sources", topic])
    if "profile" in missing:
        cmds.append(base + ["profile", topic])

    return cmds, notes


def main() -> int:
    parser = argparse.ArgumentParser(description="Batch-run missing/partial topic coverage")
    parser.add_argument(
        "--findings-dir",
        default=".planning/spikes/data/findings",
        help="Findings directory to inspect (default: .planning/spikes/data/findings)",
    )
    parser.add_argument(
        "--topics",
        nargs="*",
        default=None,
        help="Explicit topic list (space-separated). Overrides auto-discovery when provided.",
    )
    parser.add_argument(
        "--topics-file",
        default=None,
        help="Optional file containing one topic per line.",
    )
    parser.add_argument(
        "--only-controls",
        action="store_true",
        help="Restrict targets to control topics only",
    )
    parser.add_argument(
        "--controls",
        nargs="*",
        default=DEFAULT_CONTROLS,
        help="Control topic list (space-separated)",
    )
    parser.add_argument(
        "--required-layers",
        nargs="*",
        default=DEFAULT_REQUIRED_LAYERS,
        help="Layers required to consider a topic complete",
    )
    parser.add_argument(
        "--mode",
        choices=["fill", "full"],
        default="fill",
        help="fill: only run what is missing; full: run pipeline+sources+profile for each selected topic",
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Actually run commands (default is dry-run)",
    )
    parser.add_argument(
        "--no-llm",
        action="store_true",
        help="Do not pass --llm to pipeline",
    )
    parser.add_argument(
        "--mscore",
        action="store_true",
        help="Also pass --mscore when running pipeline",
    )
    args = parser.parse_args()

    findings_dir = Path(args.findings_dir)
    if not findings_dir.exists():
        print(f"error: findings directory not found: {findings_dir}", file=sys.stderr)
        return 2

    required = set(args.required_layers)
    layers = _load_topic_layers(findings_dir)

    # Ensure controls are visible even if they have zero files.
    for control in args.controls:
        layers.setdefault(control, set())

    explicit_topics = []
    if args.topics:
        explicit_topics.extend([t.strip() for t in args.topics if t.strip()])
    if args.topics_file:
        p = Path(args.topics_file)
        if not p.exists():
            print(f"error: topics file not found: {p}", file=sys.stderr)
            return 2
        explicit_topics.extend([ln.strip() for ln in p.read_text(encoding="utf-8").splitlines() if ln.strip()])

    if explicit_topics:
        candidates = set(explicit_topics)
    elif args.only_controls:
        candidates = set(args.controls)
    else:
        candidates = set(layers.keys())

    if args.mode == "fill":
        targets = sorted(t for t in candidates if not required.issubset(layers.get(t, set())))
    else:
        targets = sorted(candidates)

    mode = "EXECUTE" if args.execute else "DRY-RUN"
    print(f"mode={mode} workflow={args.mode} targets={len(targets)} required_layers={sorted(required)}")
    if not targets:
        print("Nothing to run: all selected topics already have full required coverage.")
        return 0

    use_llm = not args.no_llm
    failures = 0

    for topic in targets:
        have = sorted(layers.get(topic, set()))
        missing = sorted(required - set(have))
        print(f"\n=== {topic} ===")
        print(f"  have: {have}")
        print(f"  missing: {missing}")
        cmds, notes = _topic_commands(
            topic,
            use_llm=use_llm,
            include_mscore=args.mscore,
            mode=args.mode,
            required=required,
            have=set(have),
        )
        for note in notes:
            print(f"  note: {note}")
        for cmd in cmds:
            rc = _run(cmd, execute=args.execute)
            if rc != 0:
                failures += 1
                print(f"  command failed with exit code {rc}")

    print(f"\nDone. failures={failures}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
