"""L4 — graph-guided discovery (design §10.9): the ADL's social graph, INVERTED.

The ADL ran the graph *forwards* (co-editing → conclusion of coordination — circular, no base rate).
Here it runs strictly *downstream of content evidence and upstream only of more content testing*:

    confirmed retrofit on X  →  removal-attributed editors  →  their removal footprint elsewhere (LEAD)
        →  independent L1 re-test of each fresh candidate  →  report content-confirmed retrofits

Hard safeguards, architectural not policy (§10.9):
  1. An article is flagged ONLY by its own content signal (the L1 re-test) — never by "editor X touched it".
     The graph yields a to-check list, nothing more.
  2. False positives are expected and harmless — a prolific good editor enters the graph, their other
     articles test clean and drop out. No accusation attaches to a person from graph membership.
  3. Expansion is bound to removal actions — we follow only footprint edits that themselves removed
      substantial content (metadata `sizediff`), weighting editors by established tokens removed, not everyone who
     edited a flagged page".
  4. It flags articles (tamper areas), not people. Public data, reproducible, handed to a researcher.

The test this answers: does seeding from Zionism surface *other* genuinely-retrofitted articles the
base-rate slate didn't already contain — without the graph ever being treated as proof?
"""
import json
import pathlib
import time
import uuid
from datetime import date

import duckdb

from . import config, drift
from .corpus import Corpus
from .benchmark import ROSTER
from .config import MASS_FLOOR
from .pipeline import confirmation_is_fresh

# --- L4 knobs (a first, deliberately NARROW probe; widen once the signal is trusted) ----------------
SEED_TOP_N = 4          # seed from the top-N removal-attributed editors (by established tokens removed)
FOOTPRINT_SINCE = "2022-01-01T00:00:00Z"   # bound the footprint window (keeps the sweep polite + relevant)
FOOTPRINT_MAX_PAGES = 10                    # usercontribs continue-pages per editor (500 each) — a cap, not a scan
REMOVAL_BYTES = 1500    # a footprint edit qualifies only if it removed at least this many bytes
CANDIDATE_LIMIT = 12    # cap the to-check list re-tested with L1 (narrow probe)
MATURE_PRIOR_YEARS = 2.0  # a PIVOT only counts as stable-then-RETROFIT if the article had this long a prior
                          # BEFORE the pivot began; younger ⇒ born-in-contested (the L5 gap, not a retrofit)

_S = config.session()


def _norm(title):
    """Normalize a title for set membership (Wikipedia treats spaces/underscores as equivalent)."""
    return (title or "").replace("_", " ").strip()


def tested_set():
    """The base-rate slate already examined = the fixed benchmark ROSTER. A candidate in here is not a NEW
    discovery, so we subtract it before re-testing.

    Deliberately NOT "everything with cached snapshots": the cache grows every time L4 re-tests a candidate,
    so keying off it would make `discover` non-idempotent AND subtract L4's own prior finds — hiding
    discoveries and walking to a different candidate tier each run. The base-rate slate is a fixed set."""
    return {_norm(c["article"]) for c in ROSTER}


def _eligible_editor(editor):
    """Whether a literal public account name may become a graph node without identity inference."""
    return bool(
        editor
        and editor not in {"?", "<hidden>"}
        and not editor.lower().endswith("bot")
        and not config.ANON_IP_RE.match(editor)
    )


def seed_removing_editors(confirmation, current_horizon, top_n=SEED_TOP_N):
    """Seed from fresh exact-event attribution, excluding bots, hidden editors, and anonymous IPs."""
    if confirmation.get("status") != "confirmed":
        return [], {"reason": "not_confirmed"}
    if not confirmation_is_fresh(confirmation, current_horizon):
        return [], {"reason": "stale_confirmation"}
    episodes = confirmation.get("confirmed_episodes") or []
    if not episodes:
        return [], {"reason": "confirmed_episode_missing"}
    episode = max(episodes, key=lambda item: item.get("pwr_mass", 0))
    attribution = episode.get("attribution")
    if not isinstance(attribution, dict):
        return [], {"reason": episode.get("attribution_unavailable") or "attribution_missing"}
    rows = attribution.get("removals_by_editor") or []
    removed_count = sum(row["tokens"] for row in rows)
    if removed_count != attribution.get("removed_tokens"):
        return [], {"reason": "removal_attribution_mismatch"}
    ranked = [
        (row["editor"], row["tokens"])
        for row in sorted(rows, key=lambda row: (-row["tokens"], row["editor"]))
        if _eligible_editor(row.get("editor"))
    ]
    return ranked[:top_n], {
        "episode": episode,
        "removed_count": removed_count,
    }


def _confirmed_episode_event(article, episode):
    attribution = episode.get("attribution")
    receipt = {
        "article": article,
        "before_revid": episode.get("before_revid"),
        "after_revid": episode.get("after_revid"),
    }
    if not isinstance(attribution, dict):
        return None, [], {
            **receipt,
            "reason": episode.get("attribution_unavailable") or "attribution_missing",
        }
    removal_rows = attribution.get("removals_by_editor") or []
    if sum(row["tokens"] for row in removal_rows) != attribution.get("removed_tokens"):
        return None, [], {**receipt, "reason": "removal_attribution_mismatch"}
    eligible_rows = [row for row in removal_rows if _eligible_editor(row.get("editor"))]
    return {
        **receipt,
        "after_timestamp": episode.get("after_timestamp"),
        "durable_spine_drop": episode.get("durable_spine_drop"),
        "pwr_mass": episode.get("pwr_mass"),
        "eligible_removing_editors": [row["editor"] for row in eligible_rows],
    }, eligible_rows, None


def confirmed_event_graph(confirmations):
    """Aggregate literal editor-to-event relationships from fresh structured confirmations only."""
    events = []
    exclusions = []
    editor_nodes = {}
    for confirmation, current_horizon in confirmations:
        article = confirmation.get("article", "<unknown>")
        if confirmation.get("status") != "confirmed":
            exclusions.append({"article": article, "reason": "not_confirmed"})
            continue
        if not confirmation_is_fresh(confirmation, current_horizon):
            exclusions.append({"article": article, "reason": "stale_confirmation"})
            continue
        for episode in confirmation.get("confirmed_episodes") or []:
            event, eligible_rows, exclusion = _confirmed_episode_event(article, episode)
            if exclusion:
                exclusions.append(exclusion)
                continue
            event_key = f"{article}:{episode.get('before_revid')}→{episode.get('after_revid')}"
            events.append(event)
            for row in eligible_rows:
                node = editor_nodes.setdefault(row["editor"], {
                    "articles": set(), "events": [], "removed_tokens": 0,
                })
                node["articles"].add(article)
                node["events"].append(event_key)
                node["removed_tokens"] += row["tokens"]

    editors = [
        {
            "editor": editor,
            "article_count": len(node["articles"]),
            "event_count": len(node["events"]),
            "removed_tokens": node["removed_tokens"],
            "articles": sorted(node["articles"]),
            "events": node["events"],
        }
        for editor, node in editor_nodes.items()
    ]
    editors.sort(key=lambda node: (-node["article_count"], -node["event_count"],
                                   -node["removed_tokens"], node["editor"]))
    events.sort(key=lambda event: (event["article"], event["before_revid"] or 0, event["after_revid"] or 0))
    return {"events": events, "editors": editors, "exclusions": exclusions}


def confirmed_event_graph_report(articles_dir):
    """Read article-owned shards and return a fail-closed fresh exact-event graph."""
    root = pathlib.Path(articles_dir)
    if not root.is_dir():
        raise FileNotFoundError(f"article shard directory not found: {root}")
    confirmations = []
    exclusions = []
    artifacts = sorted(root.glob("*/findings/*.l1-confirmation.json"))
    for artifact in artifacts:
        try:
            confirmation = json.loads(artifact.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            exclusions.append({"article": artifact.parent.parent.name, "reason": f"invalid_artifact: {exc}"})
            continue
        article = confirmation.get("article", artifact.parent.parent.name)
        database = artifact.parent.parent / "provenance.duckdb"
        if not database.is_file():
            exclusions.append({"article": article, "reason": "corpus_missing"})
            continue
        try:
            con = duckdb.connect(str(database), read_only=True)
            try:
                current_horizon = Corpus(con).latest_snapshot(article)
            finally:
                con.close()
        except Exception as exc:
            exclusions.append({
                "article": article,
                "reason": f"corpus_unavailable: {type(exc).__name__}",
            })
            continue
        confirmations.append((confirmation, current_horizon))

    graph = confirmed_event_graph(confirmations)
    graph["exclusions"] = exclusions + graph["exclusions"]
    return graph


def run_confirmed_graph(articles_dir, as_json=False):
    """Print the fresh exact-event graph over article-owned shards without inferring identity."""
    root = pathlib.Path(articles_dir)
    graph = confirmed_event_graph_report(root)
    output = root / "_shared" / "findings" / "l4_confirmed_graph.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(f".{output.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(json.dumps(graph, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(output)
    finally:
        temporary.unlink(missing_ok=True)
    print("\nL4 CONFIRMED EVENT GRAPH — FRESH EXACT ATTRIBUTION ONLY")
    print("-" * 92)
    print(f"{'editor':<32} {'articles':>8} {'events':>8} {'removed tokens':>16}")
    for node in graph["editors"]:
        print(
            f"{node['editor']:<32} {node['article_count']:>8} {node['event_count']:>8} "
            f"{node['removed_tokens']:>16,}"
        )
    print("-" * 92)
    print(
        f"events={len(graph['events'])} editors={len(graph['editors'])} "
        f"exclusions={len(graph['exclusions'])} semantic_role=search_prior"
    )
    print(f"wrote {output}")
    if as_json:
        print("\n=== JSON ===")
        print(json.dumps(graph, ensure_ascii=False, indent=2))
    return graph


def _usercontribs(editor, since=FOOTPRINT_SINCE, max_pages=FOOTPRINT_MAX_PAGES):
    """Mainspace (ns0) contributions of `editor` back to `since`, newest→oldest, capped. Metadata only —
    title + byte `sizediff` + timestamp, no text (the Quarry/Replicas columns; polite to the shared API)."""
    params = {"action": "query", "format": "json", "formatversion": "2", "list": "usercontribs",
              "ucuser": editor, "ucprop": "title|sizediff|timestamp", "ucnamespace": "0",
              "uclimit": "max", "ucdir": "older", "ucend": since, "maxlag": "5"}
    rows, pages = [], 0
    while pages < max_pages:
        d = config.get_json_retrying(_S, config.ACTION, params=params, timeout=30)
        rows += d.get("query", {}).get("usercontribs", [])
        pages += 1
        cont = d.get("continue", {}).get("uccontinue")
        if not cont:
            break
        params["uccontinue"] = cont
        time.sleep(0.3)                     # be polite to the shared Action API
    return rows


def footprint(editors, tested, seed_article):
    """The removal footprint of the seed editors: per candidate article, how many seed editors removed
    substantial content there and how much. REMOVAL_BYTES is applied as an aggregate per-editor-per-article
    threshold (not a per-edit floor) so coordinated small removes are not silently discarded.
    Returns {title: {"editors": set, "removed": int, "edits": int}} for FRESH titles only (tested set +
    seed subtracted). The graph as a LEAD; nothing here flags anything."""
    seen = set(tested) | {_norm(seed_article)}
    agg = {}
    for editor, _mass in editors:
        contribs = _usercontribs(editor)
        # Aggregate ALL removals per title first; apply REMOVAL_BYTES on the total so an editor
        # removing 1400B × 50 edits is not silently excluded by the per-edit floor.
        editor_titles = {}
        for c in contribs:
            diff = -(c.get("sizediff") or 0)
            if diff > 0:
                t = _norm(c.get("title"))
                if t:
                    entry = editor_titles.setdefault(t, {"removed": 0, "edits": 0})
                    entry["removed"] += diff
                    entry["edits"] += 1
        qualifying = {t: s for t, s in editor_titles.items() if s["removed"] >= REMOVAL_BYTES}
        for t, stats in qualifying.items():
            if t in seen:
                continue
            a = agg.setdefault(t, {"editors": set(), "removed": 0, "edits": 0})
            a["editors"].add(editor)
            a["removed"] += stats["removed"]
            a["edits"] += stats["edits"]
        print(f"  {editor:<24} {len(contribs):>5} ns0 contribs (since {FOOTPRINT_SINCE[:4]}), "
              f"{len(qualifying):>4} articles with ≥{REMOVAL_BYTES}B removed (aggregate)", flush=True)
    return agg


def rank_candidates(agg, limit=CANDIDATE_LIMIT):
    """Rank the to-check list: co-occurrence first (# distinct seed editors who removed content there — the
    real graph signal), then total bytes removed. Return the top `limit` titles to re-test."""
    ranked = sorted(agg.items(), key=lambda kv: (-len(kv[1]["editors"]), -kv[1]["removed"]))
    return ranked[:limit]


def retest(con, titles):
    """Run full exact L1 analysis for each graph-surfaced candidate; coarse pivots cannot flag."""
    results = []
    for t in titles:
        print(f"\n  ── re-testing {t} (its OWN content trajectory) ──", flush=True)
        try:
            result = dict(drift.analyze(t, con=con, persist=False))
        except Exception as ex:
            results.append({"article": t, "status": "unavailable", "reason": str(ex)[:500]})
            continue
        result["article"] = t
        if result.get("status") == "confirmed":
            episodes = result.get("confirmed_episodes") or []
            top_episode = max(episodes, key=lambda episode: episode.get("pwr_mass", 0), default=None)
            first_revision = Corpus(con).first_revision_ts(t)
            candidate_start = (top_episode or {}).get("candidate_start")
            if first_revision and candidate_start:
                result["age_at_pivot"] = round(
                    (date.fromisoformat(candidate_start) - date.fromisoformat(first_revision[:10])).days / 365.25,
                    1,
                )
        results.append(result)
    return results


def _classify(r):
    """Classify an independent exact L1 result; coarse candidates can never become graph findings."""
    status = r.get("status")
    if status != "confirmed":
        return status or "unavailable"
    episodes = r.get("confirmed_episodes") or []
    top_mass = max((episode.get("pwr_mass", 0) for episode in episodes), default=0)
    if top_mass < MASS_FLOOR:
        return "confirmed-low-mass"
    if r.get("age_at_pivot") is None:
        return "confirmed-age-unknown"
    if r["age_at_pivot"] < MATURE_PRIOR_YEARS:
        return "confirmed-born-in-contested"
    return "confirmed-retrofit-lead"


def discover(article="Zionism", top_n=SEED_TOP_N, limit=CANDIDATE_LIMIT):
    """Fresh exact seed → removal footprint → independent exact L1 confirmation."""
    con = duckdb.connect(str(config.DB))
    try:
        print(f"=== L4 GRAPH-GUIDED DISCOVERY — seed: {article} ===\n", flush=True)

        confirmation = drift.load_confirmation(article)
        horizon = Corpus(con).latest_snapshot(article)
        editors, meta = seed_removing_editors(confirmation, horizon, top_n)
        if not editors:
            reason = (meta or {}).get("reason", "no eligible removing editors")
            print(f"  no fresh exact attribution seed ({reason}) — abort.")
            return
        ep = meta["episode"]
        print(
            f"seed exact event: rev {ep['before_revid']} → {ep['after_revid']}  "
            f"(~{int(ep['pwr_mass']):,} PWR-mass, drop {ep['durable_spine_drop']:.1%}); "
            f"{meta['removed_count']:,} tokens removed"
        )
        print(f"seed removing editors (top {top_n}, bots/anon excluded) — the search prior, NOT a verdict:")
        for u, n in editors:
            print(f"    {n:>6,}  {u}")

        print(f"\nremoval footprint (ns0 edits removing ≥{REMOVAL_BYTES}B since {FOOTPRINT_SINCE[:4]}):", flush=True)
        tested = tested_set()
        agg = footprint(editors, tested, article)
        ranked = rank_candidates(agg, limit)
        print(f"\nfresh candidate to-check list ({len(agg)} fresh titles; re-testing top {len(ranked)}) — "
              f"co-occurrence × bytes removed. A LEAD ONLY; each is confirmed or dropped by its OWN content:")
        for t, a in ranked:
            print(f"    [{len(a['editors'])} seed editors, {a['removed']:>8,}B removed]  {t}")

        results = retest(con, [t for t, _ in ranked])
    finally:
        con.close()

    cls = {r["article"]: _classify(r) for r in results}
    retrofit = [r for r in results if cls[r["article"]] == "confirmed-retrofit-lead"]
    born = [r for r in results if cls[r["article"]] == "confirmed-born-in-contested"]
    _print_retest(results, cls, retrofit, born)

    findings = _build_findings(article, top_n, limit, ep, meta, editors, ranked, results, cls, retrofit, born)
    config.write_findings("l4_discovery.json", _jsonable(findings))
    print(f"\nwrote findings/l4_discovery.json")
    return findings


_LABEL = {
    "confirmed-retrofit-lead": "  ← CONFIRMED RETROFIT LEAD (stable prior, own content)",
    "confirmed-born-in-contested": "  ← confirmed born-in-contested → L5",
    "confirmed-low-mass": "  (confirmed, low mass → demoted)",
}


def _print_retest(results, cls, retrofit, born):
    """Print exact L1 results; graph membership never determines the result."""
    print("\n" + "=" * 78)
    print("L1 RE-TEST — content verdicts (graph chose WHERE to look; content decides WHAT flags):")
    print("=" * 78)
    for r in results:
        c = cls[r["article"]]
        if r.get("status") == "confirmed":
            e = max(r["confirmed_episodes"], key=lambda episode: episode.get("pwr_mass", 0))
            age = r.get("age_at_pivot")
            prior = f", {age}yr prior" if age is not None else ""
            line = (
                f"CONFIRMED rev {e['before_revid']}→{e['after_revid']} "
                f"{e['pwr_mass']:,} PWR [{e['durable_spine_drop']:.1%} drop{prior}]"
                + _LABEL.get(c, "")
            )
        elif c == "not_confirmed":
            line = "NOT CONFIRMED — coarse candidate rejected by exact analysis"
        else:
            line = f"{c.upper()} — {r.get('reason', '')}"
        print(f"  {r['article']:<44} {line}")
    print("-" * 78)
    print(f"RESULT: {len(retrofit)} of {len(results)} graph-surfaced candidates are independently confirmed "
          f"stable-then-RETROFIT LEADS (PWR-mass ≥ {MASS_FLOOR:,}, ≥{MATURE_PRIOR_YEARS:.0f}yr prior).")
    if born:
        print(f"        + {len(born)} large-pivot but BORN-IN-CONTESTED ({', '.join(r['article'] for r in born)}) "
              f"— no stable prior ⇒ the L5 gap, not a retrofit.")
    if retrofit:
        print("  → the exact-attribution seed surfaced fresh retrofit-shaped articles the base-rate slate lacked —")
        print(f"    {', '.join(r['article'] for r in retrofit)}.")
        print("    Each earned its lead from its OWN trajectory, not graph membership (safeguard #1).")
    else:
        print("  → no fresh candidate content-confirmed (honest null): the graph pointed, content declined.")
    print("  DISCIPLINE: exact confirmation establishes durable content change, not bias or motive. "
          "The graph is a search prior only; L2/L5 remain necessary to investigate direction.")


def _build_findings(article, top_n, limit, ep, meta, editors, ranked, results, cls, retrofit, born):
    """Assemble the l4_discovery.json findings dict (pure — no I/O)."""
    return {
        "seed": article,
        "seed_episode": {
            "before_revid": ep["before_revid"],
            "after_revid": ep["after_revid"],
            "pwr_mass": int(ep["pwr_mass"]),
            "durable_spine_drop": ep["durable_spine_drop"],
            "tokens_removed": meta["removed_count"],
            "confirmation_status": "confirmed",
        },
        "seed_removing_editors": [{"editor": u, "tokens_removed": n} for u, n in editors],
        "params": {"top_n": top_n, "footprint_since": FOOTPRINT_SINCE, "removal_bytes": REMOVAL_BYTES,
                   "candidate_limit": limit},
        "candidates": [{"article": t, "seed_editors": sorted(a["editors"]), "removed_bytes": a["removed"],
                        "removing_edits": a["edits"]} for t, a in ranked],
        "retest": [{**r, "l4_class": cls[r["article"]]} for r in results],
        "confirmed_rewrite_leads": [r["article"] for r in retrofit + born],
        "retrofit_leads": [r["article"] for r in retrofit],
        "born_in_contested": [r["article"] for r in born],
        "semantic_role": "search_prior",
        "note": "Graph membership only selects articles for inspection. Every listed rewrite lead independently "
            "passed exact L1 confirmation; exact change still does not establish bias, motive, or coordination. "
            "Public account names are matched literally without identity inference.",
    }


def _jsonable(obj):
    """sets → sorted lists, recursively (findings JSON must be serializable)."""
    if isinstance(obj, dict):
        return {k: _jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_jsonable(v) for v in obj]
    if isinstance(obj, set):
        return sorted(obj)
    return obj
