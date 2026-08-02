"""Adjudicated benchmark (promoted from spike 009, ★#3). Scores the ground-truth roster.

Combines two OFFLINE signals per article (no WikiWho; cached data only):
  - L1 drift verdict + PWR-mass (drift.verdict_dict) — episodes ranked by PWR-mass (age-agnostic);
    recency is a descriptor (recent vs standing), NOT a demoter, so an old large capture is never buried.
    Unconfirmed candidate verdict; binary-search confirmation is separate.
  - pre-rank leads (prerank) — removal→PWR and addition→L2 routing.

Scoring rule (from the base-rate mandate): NOT "PIVOT = correct." Each case is scored against what the
right layer should say — must-flag (recall), must-NOT-flag-as-bias (benign change may register but must be
demoted / never escalated to bias), clean (stay HEALTHY), and the L5-gap cases (expected misses today).

A benchmark can only reject over-claiming, never certify "unbiased." Every flag is a lead.
"""
import json
import pathlib
import statistics

import duckdb

from . import config
from .corpus import Corpus
from .drift import verdict_dict
from .pipeline import confirmation_is_fresh
from .prerank import prerank

MASS_FLOOR = config.MASS_FLOOR

# Ground-truth roster (see [[wikipedia-drift-benchmark]]). article names must match the DB exactly.
# `src` records source-strength honestly: adjudicated study/report that names the article as its own
# analytical object (Grabowski-Klein peer-reviewed; ADL report) vs. an ArbCom case's EVIDENCE phase
# (party advocacy — the cases sanctioned editors/conduct, not individual articles). `cat` = the
# manipulation type the source alleges (removal / addition / born-biased), which sets the expected layer.
ROSTER = [
    # A — must-flag: capture by removal / retrofit (expect L1 PIVOT, PWR-mass >= floor)
    {"article": "Zionism",                      "cat": "A_removal", "src": "base-rate; PIA5-evidence+ADL", "expect": "flag: recent removal retrofit"},
    {"article": "Anti-Zionism",                 "cat": "A_removal", "src": "base-rate",                    "expect": "flag: removal retrofit"},
    {"article": "Israeli–Palestinian conflict", "cat": "A_removal", "src": "base-rate",                    "expect": "flag: removal retrofit"},
    {"article": "Hamas",                        "cat": "A_removal", "src": "ADL (de-emphasize terror)",    "expect": "flag: removal retrofit"},
    {"article": "Jedwabne pogrom",              "cat": "A_removal", "src": "Grabowski-Klein; Icewhiz-ev",  "expect": "flag: removal retrofit"},
    # B — must-flag: capture by addition / reframe / churn (expect L1 HEALTHY + pre-rank addition→L2 or churn→L2)
    {"article": "Nakba",                        "cat": "B_addition", "src": "this project (Phase 7)",       "expect": "L1 healthy + addition→L2 lead"},
    # reclassified from A_removal (S03): net-grows while shedding large PWR-mass → reframe-by-churn, an L2 case
    {"article": "Palestinian political violence","cat": "B_addition","src": "ADL (removed text ~Nov 2023)", "expect": "L1 healthy + churn→L2 lead (reframe-by-churn)"},
    {"article": "Gaza war",                     "cat": "B_addition", "src": "PIA5-evidence+ADL",            "expect": "addition→L2 (born in contested period)"},
    {"article": "Collaboration in German-occupied Poland", "cat": "B_addition", "src": "Grabowski-Klein; Icewhiz-ev", "expect": "addition→L2 reframe"},
    {"article": "Rescue of Jews by Poles during the Holocaust", "cat": "B_addition", "src": "Grabowski-Klein; Icewhiz-ev", "expect": "addition→L2 reframe"},
    {"article": "Naliboki massacre",            "cat": "B_addition", "src": "Grabowski-Klein; Icewhiz-ev", "expect": "addition→L2 (quiet article — may be sub-maturity)"},
    # C — must-flag: born-biased / long-standing (L5 only; expected MISS today — documents the L5 gap)
    {"article": "Warsaw concentration camp",    "cat": "C_l5gap", "src": "Grabowski-Klein (peer-reviewed)", "expect": "flag via L5 (born-biased ~15yr hoax) — expected L1/L2 MISS today"},
    # D — must-NOT-flag-as-bias: benign large change
    {"article": "Climate change",               "cat": "D_benign", "src": "base-rate", "expect": "may register as change; not bias; demote"},
    {"article": "Water",                        "cat": "D_benign", "src": "base-rate", "expect": "ancient/tiny; demote"},
    {"article": "Abortion",                     "cat": "D_benign", "src": "base-rate", "expect": "old; demote"},
    # E — clean controls: stay HEALTHY
    {"article": "Photosynthesis",               "cat": "E_clean", "src": "base-rate", "expect": "HEALTHY"},
    {"article": "Brontosaurus",                 "cat": "E_clean", "src": "base-rate", "expect": "HEALTHY (expansion)"},
    {"article": "Chess",                        "cat": "E_clean", "src": "base-rate", "expect": "HEALTHY"},
]


CONCENTRATION_FEATURES = (
    "durable_spine_drop",
    "pwr_mass",
    "duration_seconds",
    "top_removal_share",
    "top_replacement_share",
    "top_two_removal_share",
)

CONCENTRATION_SHARE_FEATURES = (
    "top_removal_share",
    "top_replacement_share",
    "top_two_removal_share",
)


def _validate_revision_attribution(article, pair, attribution, removal_rows, replacement_rows):
    if attribution.get("schema_version", 1) < 3:
        return
    revisions = attribution.get("revisions") or []
    gross = attribution.get("gross") or {}
    net_standing = attribution.get("net_standing") or {}
    gross_fields = {
        "removed_tokens": "gross_removed_tokens",
        "added_tokens": "gross_added_tokens",
        "restored_tokens": "restored_tokens",
    }
    for aggregate_field, revision_field in gross_fields.items():
        revision_total = sum(row.get(revision_field, 0) for row in revisions)
        if revision_total != gross.get(aggregate_field):
            raise ValueError(
                f"{article!r} event {pair} {aggregate_field} does not match revision rows"
            )

    revision_removals = {}
    revision_replacements = {}
    for row in revisions:
        account = row.get("account", "<hidden>")
        revision_removals[account] = (
            revision_removals.get(account, 0) + row.get("standing_removed_tokens", 0)
        )
        revision_replacements[account] = (
            revision_replacements.get(account, 0) + row.get("standing_added_tokens", 0)
        )
    revision_removals = {account: count for account, count in revision_removals.items() if count}
    revision_replacements = {account: count for account, count in revision_replacements.items() if count}
    displayed_removals = {row["editor"]: row["tokens"] for row in removal_rows}
    displayed_replacements = {row["editor"]: row["tokens"] for row in replacement_rows}
    if revision_removals != displayed_removals:
        raise ValueError(f"{article!r} event {pair} revision removal rows do not match editor rows")
    if revision_replacements != displayed_replacements:
        raise ValueError(f"{article!r} event {pair} revision replacement rows do not match editor rows")
    if sum(revision_removals.values()) != net_standing.get("removed_tokens"):
        raise ValueError(f"{article!r} event {pair} net-standing removal total does not match revision rows")
    if sum(revision_replacements.values()) != net_standing.get("replacement_tokens"):
        raise ValueError(f"{article!r} event {pair} net-standing replacement total does not match revision rows")


def concentration_event(article, episode):
    """Extract independently recomputable editor-attribution measures for one exact event."""
    attribution = episode.get("attribution")
    if not isinstance(attribution, dict):
        return None

    removal_rows = attribution.get("removals_by_editor") or []
    replacement_rows = attribution.get("replacement_by_editor") or []
    removed_tokens = sum(row["tokens"] for row in removal_rows)
    replacement_tokens = sum(row["tokens"] for row in replacement_rows)
    pair = f"{episode.get('before_revid')}→{episode.get('after_revid')}"
    if removed_tokens != attribution.get("removed_tokens"):
        raise ValueError(f"{article!r} event {pair} removal attribution total does not match editor rows")
    if replacement_tokens != attribution.get("replacement_tokens"):
        raise ValueError(f"{article!r} event {pair} replacement attribution total does not match editor rows")
    _validate_revision_attribution(
        article, pair, attribution, removal_rows, replacement_rows
    )

    top_removal = removal_rows[0] if removal_rows else None
    top_replacement = replacement_rows[0] if replacement_rows else None
    return {
        "article": article,
        "before_revid": episode["before_revid"],
        "after_revid": episode["after_revid"],
        "durable_spine_drop": episode["durable_spine_drop"],
        "pwr_mass": episode["pwr_mass"],
        "duration_seconds": attribution["duration_seconds"],
        "removed_tokens": removed_tokens,
        "replacement_tokens": replacement_tokens,
        "top_removal_editor": top_removal["editor"] if top_removal else None,
        "top_replacement_editor": top_replacement["editor"] if top_replacement else None,
        "top_removal_share": (
            round(top_removal["tokens"] / removed_tokens, 6) if top_removal and removed_tokens else None
        ),
        "top_replacement_share": (
            round(top_replacement["tokens"] / replacement_tokens, 6)
            if top_replacement and replacement_tokens else None
        ),
        "same_top_editor": bool(
            top_removal and top_replacement and top_removal["editor"] == top_replacement["editor"]
        ),
        "top_two_removal_share": (
            round(sum(row["tokens"] for row in removal_rows[:2]) / removed_tokens, 6)
            if removed_tokens else None
        ),
    }


def concentration_dataset(confirmations):
    """Build an unlabeled event dataset from fresh confirmations and report exclusions."""
    events = []
    exclusions = []
    for confirmation, current_horizon in confirmations:
        article = confirmation.get("article", "<unknown>")
        if confirmation.get("status") != "confirmed":
            exclusions.append({"article": article, "reason": "not_confirmed"})
            continue
        if not confirmation_is_fresh(confirmation, current_horizon):
            exclusions.append({"article": article, "reason": "stale_confirmation"})
            continue
        for episode in confirmation.get("confirmed_episodes") or []:
            event = concentration_event(article, episode)
            if event is None:
                exclusions.append({
                    "article": article,
                    "before_revid": episode.get("before_revid"),
                    "after_revid": episode.get("after_revid"),
                    "reason": episode.get("attribution_unavailable") or "attribution_missing",
                })
                continue
            events.append(event)
    return {"events": events, "exclusions": exclusions, "labels_enabled": False}


def concentration_summary(events):
    """Summarize raw feature distributions without deriving a concentration label."""
    summary = {"event_count": len(events)}
    for feature in CONCENTRATION_FEATURES:
        values = [event[feature] for event in events if event.get(feature) is not None]
        summary[feature] = {
            "count": len(values),
            "min": min(values) if values else None,
            "median": statistics.median(values) if values else None,
            "max": max(values) if values else None,
        }
    summary["same_top_editor_count"] = sum(event["same_top_editor"] for event in events)
    return summary


def concentration_readiness(summary):
    """Return blockers when editor-share distributions cannot discriminate event classes."""
    blockers = []
    for feature in CONCENTRATION_SHARE_FEATURES:
        distribution = summary[feature]
        if distribution["count"] < 2:
            blockers.append(f"{feature}: fewer than two observations")
        elif distribution["min"] == distribution["max"]:
            blockers.append(f"{feature}: no observed variance")
    return {"calibration_ready": not blockers, "calibration_blockers": blockers}


def concentration_report(articles_dir):
    """Read article-owned shards and return a freshness-checked, unlabeled calibration report."""
    confirmations = []
    exclusions = []
    artifacts = sorted(pathlib.Path(articles_dir).glob("*/findings/*.l1-confirmation.json"))
    for artifact in artifacts:
        try:
            confirmation = json.loads(artifact.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            exclusions.append({"article": artifact.parent.parent.name, "reason": f"invalid_artifact: {exc}"})
            continue

        article = confirmation.get("article", artifact.parent.parent.name)
        database = artifact.parent.parent / "provenance.duckdb"
        if not database.exists():
            exclusions.append({"article": article, "reason": "corpus_missing"})
            continue
        con = duckdb.connect(str(database), read_only=True)
        try:
            current_horizon = Corpus(con).latest_snapshot(article)
        finally:
            con.close()
        confirmations.append((confirmation, current_horizon))

    dataset = concentration_dataset(confirmations)
    dataset["exclusions"] = exclusions + dataset["exclusions"]
    dataset["summary"] = concentration_summary(dataset["events"])
    dataset.update(concentration_readiness(dataset["summary"]))
    return dataset


def run_concentration(articles_dir, as_json=False):
    """Print the Phase 4 raw-feature checkpoint; no concentration labels are enabled."""
    report = concentration_report(articles_dir)
    print("\nPHASE 4 CONCENTRATION CALIBRATION — RAW EXACT-EVENT FEATURES (UNLABELED)")
    print("-" * 118)
    print(f"{'article':<38} {'revisions':<23} {'drop':>7} {'PWR':>10} {'seconds':>9} "
          f"{'top rm':>7} {'top repl':>8} {'same':>5}")
    for event in report["events"]:
        pair = f"{event['before_revid']}→{event['after_revid']}"
        removal = event["top_removal_share"]
        replacement = event["top_replacement_share"]
        print(
            f"{event['article']:<38} {pair:<23} {event['durable_spine_drop']:>7.1%} "
            f"{event['pwr_mass']:>10,} {event['duration_seconds']:>9,} "
            f"{removal if removal is not None else 0:>7.1%} "
            f"{replacement if replacement is not None else 0:>8.1%} "
            f"{str(event['same_top_editor']):>5}"
        )
    print("-" * 118)
    print(
        f"events={len(report['events'])} exclusions={len(report['exclusions'])} "
        f"calibration_ready={report['calibration_ready']} labels_enabled=False"
    )
    for blocker in report["calibration_blockers"]:
        print(f"  calibration blocked: {blocker}")
    if report["exclusions"]:
        for exclusion in report["exclusions"]:
            print(f"  excluded {exclusion['article']}: {exclusion['reason']}")
    if as_json:
        print("\n=== JSON ===")
        print(json.dumps(report, ensure_ascii=False, indent=2))
    return report


def score_case(con, case):
    art, cat = case["article"], case["cat"]
    if case.get("pending"):
        note = "L5-gap case (expected miss until L5 built)" if cat == "C_l5gap" else "needs data (wikiwho_rs / dump)"
        return {**case, "status": "PENDING", "detail": note}

    L1 = verdict_dict(con, art)
    if L1["verdict"] == "SKIP":                      # not ingested / too few snapshots — not a FAIL
        return {**case, "status": "PENDING", "detail": "insufficient snapshots (ingest needed)"}
    pr = prerank(con, art)
    leads = pr["leads"] if pr else []
    # a "route to L2" lead can be either reframe-by-addition (net growth) or reframe-by-churn (medium but
    # highly-anomalous removal) — both mean "L1 won't catch this; hand it to the stance layer".
    l2_route = "addition→L2" in leads or "churn→L2" in leads
    v, mass = L1["verdict"], L1.get("top_mass", 0)
    rec = L1.get("top_recency", "-")

    if cat == "A_removal":
        if v == "PIVOT?" and mass >= MASS_FLOOR:
            status, detail = "PASS", f"flagged (PWR-mass {mass:,}, {rec})"
        elif v == "PIVOT?":
            status, detail = "PARTIAL", f"flagged but low mass {mass:,}"
        else:
            status, detail = "FAIL", f"missed (L1={v})"
    elif cat == "B_addition":
        route = next((x for x in ("addition→L2", "churn→L2") if x in leads), None)
        if route:
            status, detail = "PASS", f"{route} lead raised (L1={v}, as expected — L1 blind to reframe-by-{'addition' if route.startswith('addition') else 'churn'})"
        else:
            status, detail = "FAIL", f"no L2-route lead raised (L1={v}, leads={leads or 'none'})"
    elif cat == "D_benign":
        if v == "HEALTHY":
            status, detail = "PASS", "not flagged"
        elif mass < MASS_FLOOR:
            status, detail = "PASS", f"flagged as change but low mass → demoted (PWR-mass {mass:,}, {rec})"
        else:
            status, detail = "PARTIAL", (f"flagged, large (PWR-mass {mass:,}, {rec}); change is real but L1 "
                                         f"cannot separate benign from malicious → needs L2/L5")
    elif cat == "E_clean":
        if v == "HEALTHY":
            status, detail = "PASS", "HEALTHY"
        elif mass < MASS_FLOOR:
            status, detail = "PASS", f"minor pivot, low mass → demoted (PWR-mass {mass:,})"
        else:
            status, detail = "FAIL", f"false positive (PWR-mass {mass:,}, {rec})"
    elif cat == "C_l5gap":
        # born-biased / long-standing: L1 (change) + L2 (shift) are structurally blind → need L5.
        # An expected MISS is the honest measure of the gap, not a failure of L1.
        l1_flag = v == "PIVOT?" and mass >= MASS_FLOOR
        status = "L5-GAP"
        if l1_flag:
            detail = f"L1={v} (PWR-mass {mass:,}) — unexpectedly flagged by L1; investigate"
        elif l2_route:
            detail = f"L1={v} (PWR-mass {mass:,}); flagged via {next(x for x in ('addition→L2','churn→L2') if x in leads)} pre-ranker (not L1) — a lead, still needs L5 to adjudicate"
        else:
            detail = f"L1={v} (PWR-mass {mass:,}); not flagged — EXPECTED born-biased miss (needs L5 external reference)"
    else:
        status, detail = "PENDING", "uncategorised"
    return {**case, "status": status, "detail": detail, "L1": v, "pwr_mass": mass, "leads": leads}


def run(as_json=False):
    con = duckdb.connect(str(config.DB), read_only=True)
    results = [score_case(con, c) for c in ROSTER]
    con.close()

    print(f"\n{'article':<30} {'cat':<10} {'status':<8} detail")
    print("-" * 110)
    for r in results:
        print(f"{r['article']:<30} {r['cat']:<10} {r['status']:<8} {r['detail']}")

    def rate(cat, statuses=("PASS",)):
        cases = [r for r in results if r["cat"] == cat and r["status"] != "PENDING"]
        hit = [r for r in cases if r["status"] in statuses]
        return len(hit), len(cases)
    print("-" * 110)
    a_hit, a_tot = rate("A_removal")
    b_hit, b_tot = rate("B_addition")
    d_pass, d_tot = rate("D_benign")
    d_partial = [r for r in results if r["cat"] == "D_benign" and r["status"] == "PARTIAL"]
    e_hit, e_tot = rate("E_clean")
    l5gap = [r["article"] for r in results if r["cat"] == "C_l5gap"]
    pending = [r["article"] for r in results if r["status"] == "PENDING"]
    print("SUMMARY (cached subset — offline, unconfirmed):")
    print(f"  A must-flag (removal) recall : {a_hit}/{a_tot}")
    print(f"  B must-flag (addition) recall: {b_hit}/{b_tot}")
    print(f"  D benign correctly demoted   : {d_pass}/{d_tot}"
          + (f"   ⚠ {len(d_partial)} PARTIAL → L2/L5 gap: {[r['article'] for r in d_partial]}" if d_partial else ""))
    print(f"  E clean controls stay HEALTHY: {e_hit}/{e_tot}")
    print(f"  C born-biased (L5-gap)       : {l5gap} — expected L1/L2 misses, quantify the L5 gap")
    if pending:
        print(f"  PENDING (need ingest)        : {pending}")
    print("\n  Key finding: L1 (PWR-mass) achieves recall on removal/addition candidates and correctly demotes")
    print("  LOW-MASS benign change (Water, Abortion), but cannot separate LARGE benign (Climate) from large")
    print("  malicious — that separation is exactly what L2 (stance) + L5 (external reference) are for.")
    print("  Recency is context only (recent vs standing): a large STANDING distortion is a first-class find,")
    print("  never demoted by age (operator correction — long-standing distortions are a primary target).")

    if as_json:
        print("\n=== JSON ===")
        print(json.dumps(results, ensure_ascii=False, indent=2))
    return results
