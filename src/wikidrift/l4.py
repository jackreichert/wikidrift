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
import time
from datetime import date

import duckdb

from . import config, drift, provenance
from .corpus import Corpus
from .benchmark import ROSTER
from .config import MASS_FLOOR

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


def top_episode(con, article):
    """The article's top confirmed-candidate pivot episode by PWR-mass, with its snap revisions preserved.
    Offline (no WikiWho) — reuses drift.ranked_episodes; returns (peak_tuple, episode) or (None, None)."""
    *_, eps = drift.ranked_episodes(con, article)
    if not eps:
        return None, None
    e = eps[0]
    peak = (e["start"][0], e["start"][1], e["end"][0], e["end"][1], e["peak"])
    return peak, e


def seed_removing_editors(con, article, top_n=SEED_TOP_N):
    """Seed from the top editors attributed with removals in the dominant pivot, excluding bots and
    anonymous IPs. Returns [(editor, tokens_removed)]."""
    peak, e = top_episode(con, article)
    if not peak:
        return [], None
    removals_by_editor, removed_count, *_ = drift.removal_attribution(article, con, peak)
    ranked = [(u, n) for u, n in sorted(removals_by_editor.items(), key=lambda x: -x[1])
              if u and u != "?" and not u.lower().endswith("bot") and not config.ANON_IP_RE.match(u)]
    return ranked[:top_n], {"episode": e, "removed_count": removed_count}


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
    """Independent L1 content re-test — the ONLY thing that flags (safeguard #1). For each candidate:
    fetch its own timeline + persistent snapshots (hosted WikiWho) and run the offline PWR verdict.
    WikiWho coverage gaps are surfaced, never silently dropped."""
    results = []
    for t in titles:
        print(f"\n  ── re-testing {t} (its OWN content trajectory) ──", flush=True)
        try:
            provenance.ensure_sizes(con, t)
            provenance.ensure_indexes(con)
            provenance.build_snapshots(con, t)
        except Exception as ex:
            results.append({"article": t, "verdict": "ERROR", "detail": str(ex)[:120]}); continue
        corpus = Corpus(con)
        nsnap = corpus.snapshot_count(t)
        if nsnap < 3:
            nrev = corpus.revision_count(t)
            detail = ("WikiWho served <3 snapshots (coverage gap — try local wikiwho_rs)" if nrev
                      else "no revision history")
            results.append({"article": t, "verdict": "INSUFFICIENT", "snaps": nsnap, "detail": detail}); continue
        d = drift.verdict_dict(con, t)
        d["snaps"] = nsnap
        # Age of the article BEFORE its top pivot began — the stable-prior test. A large pivot on an
        # article only months old is formation churn (born-in-contested, the L5 gap), NOT a retrofit.
        if d.get("verdict") == "PIVOT?" and d.get("episodes"):
            first = corpus.first_revision_ts(t)
            if first:
                d["age_at_pivot"] = round(
                    (date.fromisoformat(d["episodes"][0]["start"]) - date.fromisoformat(first[:10])).days / 365.25, 1)
        results.append(d)
    return results


def _classify(r):
    """Interpret a re-test verdict with the born-biased discipline. Returns one of:
    'retrofit-lead' (PIVOT? + PWR-mass floor + a long stable prior), 'born-in-contested' (PIVOT? + mass
    but too young a prior → L5, not a retrofit), 'demoted' (PIVOT? below mass floor), 'healthy', or the
    raw verdict ('INSUFFICIENT'/'ERROR'/'SKIP'/'CREEP?'). All are LEADS, never confirmed verdicts."""
    v = r.get("verdict")
    if v != "PIVOT?":
        return "healthy" if v == "HEALTHY" else (v or "SKIP").lower()
    if r.get("top_mass", 0) < MASS_FLOOR:
        return "demoted"
    if r.get("age_at_pivot", 0.0) < MATURE_PRIOR_YEARS:
        return "born-in-contested"
    return "retrofit-lead"


def discover(article="Zionism", top_n=SEED_TOP_N, limit=CANDIDATE_LIMIT):
    """Full L4 probe: seed → removal footprint (LEAD) → subtract tested → independent L1 re-test.
    Prints a report and writes findings/l4_discovery.json. Every re-flag is content, never the graph."""
    con = duckdb.connect(str(config.DB))
    print(f"=== L4 GRAPH-GUIDED DISCOVERY — seed: {article} ===\n", flush=True)

    editors, meta = seed_removing_editors(con, article, top_n)
    if not editors:
        print("  no confirmed pivot / attributed removing editors to seed from — abort."); con.close(); return
    ep = meta["episode"]
    print(f"seed pivot: {ep['start'][0]} → {ep['end'][0]}  (~{int(ep['abs']):,} PWR-mass, peak {ep['peak']:.0f}%); "
          f"{meta['removed_count']:,} established tokens removed")
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
    con.close()

    cls = {r["article"]: _classify(r) for r in results}
    retrofit = [r for r in results if cls[r["article"]] == "retrofit-lead"]   # genuine stable-then-retrofit LEADS
    born = [r for r in results if cls[r["article"]] == "born-in-contested"]    # young → L5 gap, not retrofit
    _print_retest(results, cls, retrofit, born)

    findings = _build_findings(article, top_n, limit, ep, meta, editors, ranked, results, cls, retrofit, born)
    config.write_findings("l4_discovery.json", _jsonable(findings))
    print(f"\nwrote findings/l4_discovery.json")
    return findings


_LABEL = {"retrofit-lead": "  ← RETROFIT LEAD (stable prior, own content)",
          "born-in-contested": "  ← born-in-contested → L5 (no stable prior; not a retrofit)",
          "demoted": "  (low mass → demoted)"}


def _print_retest(results, cls, retrofit, born):
    """Print the L1 re-test verdict block (graph chose WHERE to look; content decides WHAT flags)."""
    print("\n" + "=" * 78)
    print("L1 RE-TEST — content verdicts (graph chose WHERE to look; content decides WHAT flags):")
    print("=" * 78)
    for r in results:
        c = cls[r["article"]]
        if r.get("verdict") == "PIVOT?":
            e = r["episodes"][0]
            age = r.get("age_at_pivot")
            prior = f", {age}yr prior" if age is not None else ""
            line = f"PIVOT? {e['start']}→{e['end']} {e['pwr_mass']:,} PWR [{e['recency']}{prior}]" + _LABEL.get(c, "")
        elif c in ("healthy", "creep?"):
            line = f"{r['verdict']}  (mean {r.get('mean_loss', 0)}%)"
        else:
            line = f"{r.get('verdict') or 'SKIP'} — {r.get('detail', r.get('reason', ''))}"
        print(f"  {r['article']:<44} {line}")
    print("-" * 78)
    print(f"RESULT: {len(retrofit)} of {len(results)} graph-surfaced candidates are fresh stable-then-RETROFIT "
          f"LEADS (PWR-mass ≥ {MASS_FLOOR:,}, ≥{MATURE_PRIOR_YEARS:.0f}yr stable prior).")
    if born:
        print(f"        + {len(born)} large-pivot but BORN-IN-CONTESTED ({', '.join(r['article'] for r in born)}) "
              f"— no stable prior ⇒ the L5 gap, not a retrofit.")
    if retrofit:
        print("  → seeding from Zionism DID surface fresh retrofit-shaped articles the base-rate slate lacked —")
        print(f"    {', '.join(r['article'] for r in retrofit)}.")
        print("    Each earned its lead from its OWN trajectory, not graph membership (safeguard #1).")
    else:
        print("  → no fresh candidate content-confirmed (honest null): the graph pointed, content declined.")
    print("  DISCIPLINE: these are unconfirmed PWR *candidates* and PIVOT = *change*, not proven bias "
          "(base-rate\n  lesson). Next precision steps: `wikidrift analyze <t>` (binary-search confirm) then L2/L5 "
          "(is the\n  change directional?). The graph is a search prior only — it never flagged anything.")


def _build_findings(article, top_n, limit, ep, meta, editors, ranked, results, cls, retrofit, born):
    """Assemble the l4_discovery.json findings dict (pure — no I/O)."""
    return {
        "seed": article,
        "seed_episode": {"start": ep["start"][0], "end": ep["end"][0], "pwr_mass": int(ep["abs"]),
                         "peak_pct": round(ep["peak"], 1), "tokens_removed": meta["removed_count"]},
        "seed_removing_editors": [{"editor": u, "tokens_removed": n} for u, n in editors],
        "params": {"top_n": top_n, "footprint_since": FOOTPRINT_SINCE, "removal_bytes": REMOVAL_BYTES,
                   "candidate_limit": limit},
        "candidates": [{"article": t, "seed_editors": sorted(a["editors"]), "removed_bytes": a["removed"],
                        "removing_edits": a["edits"]} for t, a in ranked],
        "retest": [{**r, "l4_class": cls[r["article"]]} for r in results],
        "retrofit_leads": [r["article"] for r in retrofit],
        "born_in_contested": [r["article"] for r in born],
        "note": "Graph is a LEAD only (§10.9). Flags come from each article's own L1 content signal, never "
                "from graph membership. PIVOT? = unconfirmed change candidate (run `analyze` to binary-search "
                "confirm; L2/L5 to judge direction). 'retrofit_leads' had a ≥2yr stable prior before the pivot; "
                "'born_in_contested' are large-pivot but too young to be retrofits (the L5 gap).",
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
