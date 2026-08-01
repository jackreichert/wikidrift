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
    python tools/cover_missing_topics.py --topics "Chess" "Water" --mode full --execute
    python tools/cover_missing_topics.py --topics "Brontosaurus" "Abortion" --mode fill --execute
    python tools/cover_missing_topics.py --only-controls --mode fill --execute
    python tools/cover_missing_topics.py --all-corpus --mode framing --execute
"""

from __future__ import annotations

import argparse
import concurrent.futures
import datetime as dt
import functools
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Callable, TextIO

import duckdb

from wikidrift import config, pipeline, provenance
from wikidrift.corpus import Corpus

DEFAULT_REQUIRED_LAYERS = ["receipts", "stance", "sources", "profile"]
DEFAULT_L5_MAX_LANGS = 6
MIN_ADAPTIVE_L5_MAX_LANGS = 3
DEFAULT_CONTROLS = [
    "Photosynthesis",
    "Brontosaurus",
    "Climate change",
    "Abortion",
    "Chess",
    "Water",
]
EXCLUDED_AUTO_TOPICS = {"Demo Topic"}
DEFAULT_ARTICLES_DIR = Path(".planning/spikes/data/articles")
DEFAULT_JOBS = 1


def _fresh_confirmed_shard_topics(articles_dir: Path) -> set[str]:
    """Select only fresh exact confirmations that can be upgraded without network access."""
    topics = set()
    for artifact in sorted(articles_dir.glob("*/findings/*.l1-confirmation.json")):
        try:
            confirmation = json.loads(artifact.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if confirmation.get("status") != "confirmed":
            continue
        article = confirmation.get("article")
        database = artifact.parent.parent / "provenance.duckdb"
        if not article or not database.is_file():
            continue
        try:
            con = duckdb.connect(str(database), read_only=True)
            try:
                horizon = Corpus(con).latest_snapshot(article)
            finally:
                con.close()
        except (duckdb.Error, OSError):
            continue
        if pipeline.confirmation_is_fresh(confirmation, horizon):
            topics.add(article)
    return topics


def _stale_shard_topics(articles_dir: Path) -> set[str]:
    """Select shard confirmations that no longer match their corpus or detector contract."""
    topics = set()
    for artifact in sorted(articles_dir.glob("*/findings/*.l1-confirmation.json")):
        try:
            confirmation = json.loads(artifact.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        article = confirmation.get("article")
        database = artifact.parent.parent / "provenance.duckdb"
        if not article or not database.is_file():
            continue
        try:
            con = duckdb.connect(str(database), read_only=True)
            try:
                horizon = Corpus(con).latest_snapshot(article)
            finally:
                con.close()
        except (duckdb.Error, OSError):
            continue
        if not pipeline.confirmation_is_fresh(confirmation, horizon):
            topics.add(article)
    return topics


def _fetch_extract_error_count(errors: list[dict]) -> int:
    stages = {"fetch", "extract"}
    return sum(1 for err in errors if err.get("stage") in stages)


def _load_factcheck_diagnostics(findings_dir: Path) -> dict[str, dict]:
    latest: dict[str, tuple[float, dict]] = {}
    for p in findings_dir.glob("*.factcheck.json"):
        try:
            payload = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        topic = (payload.get("article") or "").strip()
        if not topic:
            continue
        diagnostics = payload.get("diagnostics") or {}
        if not isinstance(diagnostics, dict):
            diagnostics = {}
        record = {
            "langs_count": len(payload.get("langs") or []),
            "effective_count": len(diagnostics.get("effective_langs") or []),
            "error_count": _fetch_extract_error_count(diagnostics.get("errors") or []),
        }
        mtime = p.stat().st_mtime
        prev = latest.get(topic)
        if prev is None or mtime >= prev[0]:
            latest[topic] = (mtime, record)
    return {topic: record for topic, (_, record) in latest.items()}


def _adaptive_l5_cap(topic: str, diagnostics: dict[str, dict], default_cap: int) -> tuple[int, str]:
    record = diagnostics.get(topic)
    if not record:
        return default_cap, f"L5 cap adaptive: no prior diagnostics; using {default_cap}"

    attempted = int(record.get("langs_count") or 0)
    effective = int(record.get("effective_count") or 0)
    errors = int(record.get("error_count") or 0)

    observed = max(attempted, effective + errors, 1)
    success = effective / observed

    cap = max(MIN_ADAPTIVE_L5_MAX_LANGS, min(default_cap, effective + 1))
    if effective < 2 or success < 0.50:
        cap = MIN_ADAPTIVE_L5_MAX_LANGS
    elif success < 0.70:
        cap = min(cap, 4)
    elif success < 0.85:
        cap = min(cap, 5)
    else:
        cap = min(default_cap, max(cap, min(default_cap, effective + 1)))

    note = (
        "L5 cap adaptive: "
        f"attempted={attempted} effective={effective} errors={errors} "
        f"success={success:.2f} -> {cap}"
    )
    return cap, note


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
    for p in findings_dir.glob("*.framing.json"):
        try:
            payload = json.loads(p.read_text(encoding="utf-8"))
            add((payload.get("article") or "").strip(), "framing")
        except Exception:
            continue

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


def _project_command(args: list[str]) -> list[str]:
    """Run Wikidrift with the same Python environment as this batch process."""
    return [sys.executable, "-m", "wikidrift.cli", *args]


def _canonicalize_topics(topics: list[str], resolver=provenance.resolve_article_title):
    """Resolve aliases and retain one target per canonical MediaWiki article."""
    canonical_topics = []
    identities = []
    seen = set()
    for topic in topics:
        resolved = resolver(topic)
        canonical = resolved.canonical_title
        identities.append(resolved)
        if canonical not in seen:
            seen.add(canonical)
            canonical_topics.append(canonical)
    return canonical_topics, identities


def _write_article_identities(articles_dir: Path, identities: list[provenance.ResolvedArticle]):
    """Persist requested aliases beside the canonical article-owned shard."""
    grouped = {}
    for identity in identities:
        record = grouped.setdefault(identity.canonical_title, {
            "canonical_title": identity.canonical_title,
            "page_id": identity.page_id,
            "requested_titles": [],
        })
        if identity.requested_title not in record["requested_titles"]:
            record["requested_titles"].append(identity.requested_title)
    for canonical, record in grouped.items():
        data_dir = articles_dir / config.slugify(canonical)
        data_dir.mkdir(parents=True, exist_ok=True)
        destination = data_dir / "article-identity.json"
        temporary = destination.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temporary, destination)


def _stage_name(command: list[str]) -> str:
    for entrypoint in ("wikidrift.cli", "wikidrift"):
        try:
            return command[command.index(entrypoint) + 1]
        except (ValueError, IndexError):
            continue
    raise ValueError(f"not a wikidrift command: {command!r}")


def _load_completed_stages(state_path: Path) -> list[str]:
    try:
        payload = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    stages = payload.get("completed_stages") or []
    return [stage for stage in stages if isinstance(stage, str)]


def _write_coverage_state(state_path: Path, topic: str, completed_stages: list[str]) -> None:
    payload = {
        "article": topic,
        "updated_ts": dt.datetime.now(dt.timezone.utc).isoformat(),
        "completed_stages": completed_stages,
    }
    temporary = state_path.with_suffix(".json.tmp")
    try:
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temporary, state_path)
    except OSError as exc:
        temporary.unlink(missing_ok=True)
        raise OSError(f"could not publish coverage state for {topic!r}") from exc


def _emit_topic_output(
    topic: str,
    line: str,
    output_lock: threading.Lock,
    output_stream: TextIO | None = None,
) -> None:
    stream = output_stream or sys.stdout
    rendered = line if line.endswith("\n") else f"{line}\n"
    with output_lock:
        stream.write(f"[{topic}] {rendered}")
        stream.flush()


def _run_streaming_command(
    command: list[str],
    *,
    env: dict[str, str],
    log: TextIO,
    topic: str,
    output_lock: threading.Lock,
    output_stream: TextIO | None = None,
    process_factory: Callable = subprocess.Popen,
) -> subprocess.CompletedProcess:
    """Run a command while teeing complete output lines to its log and stdout."""
    process = process_factory(
        command,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    if process.stdout is None:
        raise RuntimeError(f"could not capture output for {command!r}")
    try:
        for line in process.stdout:
            log.write(line)
            log.flush()
            _emit_topic_output(topic, line, output_lock, output_stream)
    except BaseException as exc:
        log.write(f"PROCESS KILLED: {type(exc).__name__}\n")
        log.flush()
        process.kill()
        process.wait()
        raise
    return subprocess.CompletedProcess(command, process.wait())


def _resume_enabled(mode: str, requested: bool | None) -> bool:
    """Resolve mode-aware resume defaults while preserving explicit flags."""
    if requested is not None:
        return requested
    return mode != "refresh"


def _run_topic_commands(
    topic: str,
    commands: list[list[str]],
    articles_dir: Path,
    resume: bool,
    runner: Callable | None = None,
    cancellation: threading.Event | None = None,
    output_lock: threading.Lock | None = None,
) -> dict:
    """Run one topic's stages sequentially with resumable article-owned state."""
    data_dir = articles_dir / config.slugify(topic)
    log_dir = data_dir / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / "coverage.log"
    state_path = data_dir / "coverage-state.json"
    completed_stages = _load_completed_stages(state_path)
    skipped_stages = []
    stages = []

    environment = os.environ.copy()
    environment["WIKIDRIFT_DATA_DIR"] = str(data_dir)
    environment["PYTHONUNBUFFERED"] = "1"
    output_lock = output_lock or threading.Lock()
    with log_path.open("a", encoding="utf-8") as log:
        log.write(f"\n=== coverage run {dt.datetime.now(dt.timezone.utc).isoformat()} ===\n")
        for command in commands:
            if cancellation is not None and cancellation.is_set():
                log.write("CANCEL before next stage\n")
                break
            stage = _stage_name(command)
            if resume and stage in completed_stages:
                skipped_stages.append(stage)
                log.write(f"SKIP completed stage: {stage}\n")
                if runner is None:
                    _emit_topic_output(topic, f"SKIP completed stage: {stage}", output_lock)
                continue
            log.write(f"$ {' '.join(command)}\n")
            log.flush()
            if runner is None:
                _emit_topic_output(topic, f"$ {' '.join(command)}", output_lock)
            started = time.monotonic()
            if runner is None:
                completed = _run_streaming_command(
                    command,
                    env=environment,
                    log=log,
                    topic=topic,
                    output_lock=output_lock,
                )
            else:
                completed = runner(
                    command,
                    env=environment,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                )
            exit_code = int(completed.returncode)
            stages.append({
                "command": stage,
                "elapsed_seconds": round(time.monotonic() - started, 3),
                "exit_code": exit_code,
            })
            if exit_code != 0:
                break
            if stage not in completed_stages:
                completed_stages.append(stage)
            _write_coverage_state(state_path, topic, completed_stages)

    return {
        "article": topic,
        "succeeded": all(stage["exit_code"] == 0 for stage in stages),
        "analysis_outcome": _analysis_outcome(data_dir, topic),
        "stages": stages,
        "skipped_stages": skipped_stages,
        "log_path": str(log_path),
    }


def _analysis_outcome(data_dir: Path, topic: str) -> str:
    """Return confirmed, not_confirmed, unavailable, or unknown independently of process status."""
    artifact = data_dir / "findings" / f"{config.slugify(topic)}.l1-confirmation.json"
    try:
        payload = json.loads(artifact.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return "unknown"
    status = payload.get("status")
    return status if status in {"confirmed", "not_confirmed", "unavailable"} else "unknown"


def _run_topic_item(item: tuple[str, list[list[str]]], *, articles_dir: Path,
                    resume: bool, cancellation: threading.Event | None = None,
                    output_lock: threading.Lock | None = None) -> dict:
    topic, commands = item
    try:
        return _run_topic_commands(
            topic, commands, articles_dir, resume,
            cancellation=cancellation,
            output_lock=output_lock,
        )
    except Exception as exc:
        log_path = articles_dir / config.slugify(topic) / "logs" / "coverage.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a", encoding="utf-8") as log:
            log.write(f"WORKER ERROR: {type(exc).__name__}: {exc}\n")
        if output_lock is not None:
            _emit_topic_output(
                topic, f"WORKER ERROR: {type(exc).__name__}: {exc}", output_lock,
            )
        return {
            "article": topic,
            "succeeded": False,
            "stages": [],
            "skipped_stages": [],
            "log_path": str(log_path),
            "error": str(exc),
        }


def _run_topics_parallel(
    topic_commands: list[tuple[str, list[list[str]]]],
    articles_dir: Path,
    jobs: int,
    resume: bool,
    executor_factory: Callable = concurrent.futures.ThreadPoolExecutor,
) -> list[dict]:
    """Run article-owned workers with bounded concurrency and input-order results."""
    slugs = [config.slugify(topic) for topic, _ in topic_commands]
    if len(slugs) != len(set(slugs)):
        raise ValueError("duplicate topics would share an article shard")

    cancellation = threading.Event()
    output_lock = threading.Lock()
    worker = functools.partial(
        _run_topic_item,
        articles_dir=articles_dir,
        resume=resume,
        cancellation=cancellation,
        output_lock=output_lock,
    )
    with executor_factory(max_workers=jobs) as executor:
        try:
            return list(executor.map(worker, topic_commands))
        except BaseException:
            cancellation.set()
            executor.shutdown(wait=True, cancel_futures=True)
            raise


def _write_cost_report(findings_dir: Path, topic: str, stages: list[dict]) -> dict:
    """Persist measurable per-article cost without inventing infrastructure prices."""
    framing_path = findings_dir / f"{config.slugify(topic)}.framing.json"
    try:
        framing = json.loads(framing_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        framing = {}
    usage = framing.get("llm_usage") or {
        "calls": 0, "input_tokens": 0, "output_tokens": 0,
        "estimated_usd": None, "all_calls_priced": False, "records": [],
    }
    report = {
        "article": topic,
        "run_ts": dt.datetime.now(dt.timezone.utc).isoformat(),
        "workflow": "confirmed_framing_refresh",
        "succeeded": bool(stages) and all(stage["exit_code"] == 0 for stage in stages),
        "elapsed_seconds": round(sum(stage["elapsed_seconds"] for stage in stages), 3),
        "stages": stages,
        "llm_usage": usage,
        "estimated_external_usd": usage.get("estimated_usd"),
        "estimate_scope": (
            "LLM token charges only. Wikipedia and WikiWho APIs are public and unpriced here; "
            "machine time, storage, payment fees, taxes, failed-response charges, and service margin "
            "are excluded."
        ),
    }
    findings_dir.mkdir(parents=True, exist_ok=True)
    (findings_dir / f"{config.slugify(topic)}.cost.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def _pipeline_cmd(topic: str, use_llm: bool, include_mscore: bool, include_framing: bool = False,
                  l5_max_langs: int | None = None) -> list[str]:
    cmd = _project_command(["pipeline", topic])
    if use_llm:
        cmd.append("--llm")
    if include_mscore:
        cmd.append("--mscore")
    if include_framing:
        cmd.append("--framing")
    return cmd


def _topic_commands(topic: str, use_llm: bool, include_mscore: bool, include_framing: bool, mode: str,
                    l5_max_langs: int | None,
                    required: set[str], have: set[str]) -> tuple[list[list[str]], list[str]]:
    """Return (commands, notes) for this topic under the selected mode."""
    base = _project_command([])
    cmds: list[list[str]] = []
    notes: list[str] = []

    if mode == "framing":
        cmds.append(base + ["analyze", topic])
        cmds.append(base + ["framing", topic])
        return cmds, notes

    if mode == "pipeline":
        cmds.append(base + ["analyze", topic])
        cmds.append(_pipeline_cmd(topic, use_llm=use_llm, include_mscore=include_mscore,
                                  include_framing=include_framing))
        return cmds, notes

    if mode == "attribution":
        cmds.append(base + ["backfill-attribution", topic])
        return cmds, notes

    if mode == "refresh":
        cmds.append(base + ["analyze", topic])
        return cmds, notes

    if mode == "full":
        cmds.append(base + ["analyze", topic])
        cmds.append(_pipeline_cmd(topic, use_llm=use_llm, include_mscore=include_mscore,
                                  include_framing=include_framing))
        if use_llm:
            cmds.append(base + ["crosslingual", topic])
        cmds.append(base + ["sources", topic])
        cmds.append(base + ["profile", topic])
        return cmds, notes

    # mode == fill: run the smallest command set that can cover missing layers.
    missing = required - have
    needs_framing = "framing" in missing
    needs_upper_layers = needs_framing or bool({"receipts", "stance"} & missing)
    if needs_upper_layers:
        cmds.append(base + ["analyze", topic])
    if needs_framing:
        cmds.append(_pipeline_cmd(topic, use_llm=use_llm, include_mscore=include_mscore,
                                  include_framing=include_framing))

    if use_llm and {"receipts", "stance"} & missing:
        cmds.append(base + ["crosslingual", topic])

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
        "--all-corpus",
        action="store_true",
        help="Select every article with at least three snapshots in the local DuckDB corpus",
    )
    parser.add_argument(
        "--all-shards",
        action="store_true",
        help=("Select article-owned shards without network title resolution: fresh confirmed shards "
              "normally, or stale shards in refresh mode"),
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
                    choices=["fill", "full", "framing", "pipeline", "attribution", "refresh"],
        default="fill",
          help=("fill: only run missing layers; pipeline: run analyze then pipeline; "
              "attribution: backfill current confirmed exact pairs; "
                            "refresh: rerun stale exact analysis with live per-article logs; "
              "full: run pipeline+sources+profile; "
              "framing: run analyze then Framing Lite to create confirmed temporal receipts"),
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Actually run commands (default is dry-run)",
    )
    parser.add_argument(
        "--jobs",
        type=int,
        default=DEFAULT_JOBS,
        help=f"Maximum article workers (default: {DEFAULT_JOBS})",
    )
    parser.add_argument(
        "--articles-dir",
        type=Path,
        default=DEFAULT_ARTICLES_DIR,
        help=f"Root for isolated article data (default: {DEFAULT_ARTICLES_DIR})",
    )
    parser.add_argument(
        "--resume",
        action=argparse.BooleanOptionalAction,
        default=None,
        help=("Skip stages recorded as successful in each article's coverage-state.json "
              "(default: off for refresh, on otherwise)"),
    )
    parser.add_argument(
        "--no-llm",
        action="store_true",
        help="Do not pass --llm to pipeline",
    )
    parser.add_argument(
        "--mscore",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Pass --mscore when running pipeline (default: on; use --no-mscore to skip)",
    )
    parser.add_argument(
        "--l5-max-langs",
        type=int,
        default=DEFAULT_L5_MAX_LANGS,
        help=(
            "When --llm is active, cap L5 factcheck editions for stability "
            f"(default: {DEFAULT_L5_MAX_LANGS}; set 0 for no cap)"
        ),
    )
    parser.add_argument(
        "--l5-cap-policy",
        choices=["adaptive", "fixed"],
        default="adaptive",
        help=(
            "Cap strategy when --l5-max-langs > 0: "
            "adaptive uses latest topic diagnostics, fixed always uses --l5-max-langs"
        ),
    )
    parser.add_argument(
        "--framing",
        action="store_true",
        help="Run L5 Framing Lite via pipeline --framing in fill/full mode (opt-in; needs an LLM key)",
    )
    args = parser.parse_args()

    if args.jobs < 1:
        parser.error("--jobs must be at least 1")

    selectors = sum(map(bool, (
        args.topics, args.topics_file, args.only_controls, args.all_corpus, args.all_shards,
    )))
    if selectors > 1:
        parser.error(
            "choose only one of --topics, --topics-file, --only-controls, --all-corpus, or --all-shards"
        )
    if args.mode == "framing" and args.no_llm:
        parser.error("--mode framing requires an LLM; remove --no-llm")
    if args.mode == "refresh" and not (args.all_shards or args.topics or args.topics_file):
        parser.error("--mode refresh requires --all-shards, --topics, or --topics-file")

    findings_dir = Path(args.findings_dir)
    if not findings_dir.exists():
        print(f"error: findings directory not found: {findings_dir}", file=sys.stderr)
        return 2

    required = set(args.required_layers)
    if args.framing:
        required.add("framing")
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

    identities = []
    if explicit_topics:
        try:
            explicit_topics, identities = _canonicalize_topics(explicit_topics)
        except (OSError, ValueError) as exc:
            print(f"error: could not resolve article title: {exc}", file=sys.stderr)
            return 2
        for identity in identities:
            if identity.requested_title != identity.canonical_title:
                print(f"resolved redirect: {identity.requested_title} -> {identity.canonical_title}")

    if args.all_shards:
        candidates = (
            _stale_shard_topics(args.articles_dir)
            if args.mode == "refresh"
            else _fresh_confirmed_shard_topics(args.articles_dir)
        )
    elif args.all_corpus:
        if not config.DB.exists():
            print(f"error: token corpus not found: {config.DB}", file=sys.stderr)
            return 2
        con = duckdb.connect(str(config.DB), read_only=True)
        try:
            candidates = set(Corpus(con).articles_with_snapshots(3))
        finally:
            con.close()
    elif explicit_topics:
        candidates = set(explicit_topics)
    elif args.only_controls:
        candidates = set(args.controls)
    else:
        candidates = set(layers.keys()) - EXCLUDED_AUTO_TOPICS

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
    factcheck_diagnostics = _load_factcheck_diagnostics(findings_dir)
    topic_commands = []

    for topic in targets:
        have = sorted(layers.get(topic, set()))
        missing = sorted(required - set(have))
        print(f"\n=== {topic} ===")
        print(f"  have: {have}")
        print(f"  missing: {missing}")
        topic_l5_max_langs = args.l5_max_langs or None
        if args.mode != "framing" and use_llm and topic_l5_max_langs and args.l5_cap_policy == "adaptive":
            topic_l5_max_langs, cap_note = _adaptive_l5_cap(topic, factcheck_diagnostics, args.l5_max_langs)
            print(f"  note: {cap_note}")
        cmds, notes = _topic_commands(
            topic,
            use_llm=use_llm,
            include_mscore=args.mscore,
            include_framing=args.framing,
            mode=args.mode,
            l5_max_langs=topic_l5_max_langs,
            required=required,
            have=set(have),
        )
        for note in notes:
            print(f"  note: {note}")
        topic_commands.append((topic, cmds))
        if not args.execute:
            for cmd in cmds:
                _run(cmd, execute=False)

    if not args.execute:
        print("\nDone. failures=0")
        return 0

    _write_article_identities(args.articles_dir.resolve(), identities)
    print(f"\nRunning {len(topic_commands)} topic(s) with jobs={args.jobs} in {args.articles_dir}")
    resume = _resume_enabled(args.mode, args.resume)
    if args.mode == "refresh" and not resume:
        print("Refresh mode reruns stale analyze stages regardless of prior coverage state.")
    try:
        results = _run_topics_parallel(
            topic_commands=topic_commands,
            articles_dir=args.articles_dir.resolve(),
            jobs=args.jobs,
            resume=resume,
        )
    except KeyboardInterrupt:
        print("\nInterrupted; completed stages remain resumable.", file=sys.stderr)
        return 130

    failures = 0
    unavailable = 0
    for result in results:
        analysis_outcome = result.get("analysis_outcome", "unknown")
        if not result["succeeded"]:
            outcome = "FAIL"
            failures += 1
        elif analysis_outcome == "unavailable":
            outcome = "UNAVAILABLE"
            unavailable += 1
        elif analysis_outcome in {"confirmed", "not_confirmed"}:
            outcome = f"PASS {analysis_outcome}"
        else:
            outcome = "PASS"
        stage_summary = [(stage["command"], stage["exit_code"]) for stage in result["stages"]]
        skipped = result["skipped_stages"]
        print(f"{outcome} {result['article']}: stages={stage_summary} skipped={skipped} "
              f"log={result['log_path']}")
        if args.mode == "framing":
            topic_findings = args.articles_dir.resolve() / config.slugify(result["article"]) / "findings"
            report = _write_cost_report(topic_findings, result["article"], result["stages"])
            estimate = report["estimated_external_usd"]
            estimate_text = f"${estimate:.6f}" if estimate is not None else "unavailable (configure pricing)"
            outcome = "succeeded" if report["succeeded"] else "failed"
            print(f"  cost report: {outcome}, {report['elapsed_seconds']:.1f}s, LLM estimate {estimate_text}")

    print(f"\nDone. failures={failures} unavailable={unavailable}")
    return 1 if failures or unavailable else 0


if __name__ == "__main__":
    raise SystemExit(main())
