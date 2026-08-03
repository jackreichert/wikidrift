"""viewer/export_l3.py — export L3 render data (before/after diff + blame) for the viewer.

Uses the wikidrift package over PUBLIC APIs only (Action + WikiWho) — no ANTHROPIC key — to emit
per-article JSON the static site renders:

  <slug>.diff.json  — same-language before/after-pivot prose (the rewrite made visible as a diff).
  <slug>.blame.json — WhoColor-style authorship of the current lead: each span attributed to the
                      editor + origin date, via WikiWho token o_rev_id joined to the revision timeline.

Fulfills L3 (spike 003, highlight-overlay). Writes to viewer/data/. Run: python viewer/export_l3.py
"""
import json
import pathlib
import sys
from collections import Counter

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))
import duckdb  # noqa: E402
from wikidrift import config, drift, provenance, trust  # noqa: E402
from wikidrift.corpus import Corpus  # noqa: E402
from wikidrift.l5_crosslingual import fetch_asof  # noqa: E402
from wikidrift.pipeline import confirmation_is_fresh  # noqa: E402
from wikidrift.stance import prose_at  # noqa: E402

DATA = pathlib.Path(__file__).resolve().parent / "data"
BLAME_TOKENS = 1500                        # blame the lead only (full-article is a v2 paginated view)

# Simple before/after diff fallback (articles with no L1 pivot). None = auto (2yr before onset).
DIFF = {"Warsaw concentration camp": "2018-06-01"}
BLAME = ["Zionism"]


def published_article_sources(findings_dir=None, articles_dir=None):
    """Return each exportable article's owning paths; article shards override legacy findings."""
    use_default_shards = findings_dir is None and articles_dir is None
    findings_dir = pathlib.Path(findings_dir or config.FINDINGS)
    directories = [findings_dir]
    if use_default_shards:
        articles_dir = config.ARTICLES_DIR
    if articles_dir:
        articles_dir = pathlib.Path(articles_dir)
        if articles_dir.exists():
            directories.extend(
                article_dir / "findings"
                for article_dir in sorted(articles_dir.iterdir())
                if article_dir.is_dir() and article_dir.name != "_shared"
            )

    sources = {}
    for directory in directories:
        if not directory.exists():
            continue
        paths = list(directory.glob("*.profile.json"))
        paths.extend(directory.glob("*.l1-confirmation.json"))
        for path in paths:
            try:
                article = json.loads(path.read_text(encoding="utf-8")).get("article")
            except (OSError, json.JSONDecodeError):
                continue
            if article:
                sources[article] = {
                    "findings_dir": directory,
                    "database": directory.parent / "provenance.duckdb",
                }
    return dict(sorted(sources.items()))


def published_articles(findings_dir=None, articles_dir=None):
    """Return the article names eligible for L3 export."""
    return list(published_article_sources(findings_dir, articles_dir))


def _before_date(article):
    con = duckdb.connect(str(config.DB), read_only=True)
    try:
        eps = drift.verdict_dict(con, article).get("episodes", [])
    finally:
        con.close()
    if eps:
        start = min(eps, key=lambda e: e["age_years"])["start"]      # YYYY-MM-DD
        return f"{int(start[:4]) - 2}{start[4:]}", f"L1 pivot onset {start} − 2yr"
    return "2022-07-01", "fallback (L1=HEALTHY)"


def export_diff(article, before=None, lang="en"):
    src = "override"
    if before is None:
        before, src = _before_date(article)
    rb, tsb, _, pb = fetch_asof(lang, article, f"{before}T00:00:00Z")
    ra, tsa, _, pa = fetch_asof(lang, article, None)
    out = {"article": article, "lang": lang, "before_source": src,
           "before": {"revid": rb, "ts": tsb, "date": before, "text": pb},
           "after": {"revid": ra, "ts": tsa, "text": pa}}
    DATA.mkdir(parents=True, exist_ok=True)
    (DATA / f"{config.slugify(article)}.diff.json").write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    print(f"  diff {article}: before {before} ({len(pb):,} chars) vs now ({len(pa):,} chars)")


def export_blame(article):
    con = duckdb.connect(str(config.DB), read_only=True)
    try:
        corpus = Corpus(con)
        latest = corpus.latest_snap_rev(article)
        users = {rid: (u, ts) for rid, ts, u in corpus.revision_rows(article)}
    finally:
        con.close()
    if not latest:
        print(f"  blame {article}: no snapshot"); return
    rev = latest[0]
    toks = provenance.tokens_at(article, rev)[:BLAME_TOKENS]
    spans, cur = [], None
    for t in toks:
        orev, s = t["o_rev_id"], t.get("str", "")
        u, ts = users.get(orev, ("?", None))
        if cur and cur["o_rev_id"] == orev:
            cur["text"] += " " + s
        else:
            cur = {"o_rev_id": orev, "editor": u, "o_time": (ts or "")[:10], "text": s}
            spans.append(cur)
    out = {"article": article, "revid": rev, "tokens": len(toks), "spans": spans}
    DATA.mkdir(parents=True, exist_ok=True)
    (DATA / f"{config.slugify(article)}.blame.json").write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    print(f"  blame {article}: {len(toks)} tokens (lead) → {len(spans)} authored spans, rev {rev}")


def _top(names):
    names = [n for n in names if n and n != "?"]
    return Counter(names).most_common(1)[0][0] if names else "?"


def _word_authors(toks, authors):
    """{lowercased word -> dominant editor} from word tokens only (skips markup/refs/punctuation),
    so the readable prose diff can be annotated with who added/wrote each passage (best-match)."""
    counts = {}
    for t in toks:
        s = t.get("str", "")
        if len(s) < 3 or not s.isalpha():
            continue
        counts.setdefault(s.lower(), Counter())[authors.get(t.get("o_rev_id"), "?")] += 1
    return {w: c.most_common(1)[0][0] for w, c in counts.items()}


def _report_missing_authorship(article, revision, tokens):
    if not tokens:
        print(f"  authorship {article} revision {revision}: unavailable; redline retained")


def _current_horizon(article, database=None):
    con = duckdb.connect(str(database or config.DB), read_only=True)
    try:
        return Corpus(con).latest_snapshot(article)
    finally:
        con.close()


def _confirmation_trust(article, confirmation, database=None):
    con = duckdb.connect(str(database or config.DB), read_only=True)
    try:
        return trust.resolve_artifact_trust(con, article, confirmation, "l1-confirmation")
    finally:
        con.close()


def _revision_authors(article, database=None):
    con = duckdb.connect(str(database or config.DB), read_only=True)
    try:
        return Corpus(con).revision_editor(article)
    finally:
        con.close()


def _exact_pivots(article, confirmation, database=None):
    authors = _revision_authors(article, database)
    pivots = []
    unavailable_pairs = []
    for episode in confirmation.get("confirmed_episodes") or []:
        before_revid = episode.get("before_revid")
        after_revid = episode.get("after_revid")
        before_text = prose_at(before_revid)
        after_text = prose_at(after_revid)
        if not before_text or not after_text:
            unavailable_pairs.append(f"{before_revid}→{after_revid}")
            continue
        before_tokens = provenance.tokens_at(article, before_revid)
        after_tokens = provenance.tokens_at(article, after_revid)
        _report_missing_authorship(article, before_revid, before_tokens)
        _report_missing_authorship(article, after_revid, after_tokens)
        pivots.append({
            "start": episode.get("before_timestamp", "")[:10],
            "end": episode.get("after_timestamp", "")[:10],
            "peak_pct": round(100 * (episode.get("durable_spine_drop") or 0), 2),
            "pwr_mass": episode.get("pwr_mass"),
            "before_rev": before_revid,
            "after_rev": after_revid,
            "status": "confirmed",
            "metric": "exact_durable_spine_drop",
            "duration_seconds": episode.get("duration_seconds"),
            "attribution": episode.get("attribution"),
            "attribution_unavailable": episode.get("attribution_unavailable"),
            "before_text": before_text,
            "after_text": after_text,
            "authors_before": _word_authors(before_tokens, authors),
            "authors_after": _word_authors(after_tokens, authors),
        })
    return pivots, unavailable_pairs


def _candidate_pivots(article, confirmation, database=None):
    """Materialize each evaluated coarse candidate as an inspectable redline."""
    authors = _revision_authors(article, database)
    pivots = []
    unavailable_pairs = []
    for candidate in confirmation.get("evaluated_candidates") or []:
        before_revid = candidate.get("candidate_before_revid")
        after_revid = candidate.get("candidate_after_revid")
        before_text = prose_at(before_revid)
        after_text = prose_at(after_revid)
        if not before_text or not after_text:
            unavailable_pairs.append(f"{before_revid}→{after_revid}")
            continue
        before_tokens = provenance.tokens_at(article, before_revid)
        after_tokens = provenance.tokens_at(article, after_revid)
        _report_missing_authorship(article, before_revid, before_tokens)
        _report_missing_authorship(article, after_revid, after_tokens)
        decision = candidate.get("decision") or "rejected"
        pivots.append({
            "start": candidate.get("candidate_start"),
            "end": candidate.get("candidate_end"),
            "peak_pct": candidate.get("peak_pct"),
            "pwr_mass": candidate.get("pwr_mass"),
            "before_rev": before_revid,
            "after_rev": after_revid,
            "status": "confirmed" if decision == "confirmed" else "rejected",
            "decision": decision,
            "rejection_reason": candidate.get("rejection_reason"),
            "durable_spine_drop": candidate.get("durable_spine_drop"),
            "metric": "persistence_weighted_loss",
            "before_text": before_text,
            "after_text": after_text,
            "authors_before": _word_authors(before_tokens, authors),
            "authors_after": _word_authors(after_tokens, authors),
        })
    return pivots, unavailable_pairs


def _unavailable_export(output, article, state, reason):
    output.unlink(missing_ok=True)
    print(f"  pivots {article}: {state} (L1={reason})")
    return {"state": state, "reason": reason}


def _export_candidate_pivots(article, confirmation, output, database=None):
    trust_decision = _confirmation_trust(article, confirmation, database)
    if trust_decision["status"] != "published":
        return _unavailable_export(
            output,
            article,
            "unavailable",
            f"artifact withheld: {trust_decision['reason']}",
        )
    horizon = _current_horizon(article, database)
    if not confirmation_is_fresh(confirmation, horizon):
        return _unavailable_export(output, article, "unavailable", "stale exact confirmation")
    candidates = confirmation.get("evaluated_candidates") or []
    if candidates:
        pivots, unavailable_pairs = _candidate_pivots(article, confirmation, database)
    elif confirmation.get("status") == "confirmed":
        pivots, unavailable_pairs = _exact_pivots(article, confirmation, database)
    else:
        return _unavailable_export(
            output,
            article,
            "none",
            confirmation.get("reason") or confirmation.get("status"),
        )
    if not pivots:
        pairs = ", ".join(unavailable_pairs) or "none recorded"
        reason = f"candidate pair could not be materialized: {pairs}"
        return _unavailable_export(output, article, "unavailable", reason)
    DATA.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps({
        "article": article,
        "corpus_horizon": confirmation.get("corpus_horizon"),
        "pivots": pivots,
        "unavailable_pairs": unavailable_pairs,
    }, ensure_ascii=False), encoding="utf-8")
    print(f"  pivots {article}: {len(pivots)} candidate redline(s)")
    return {"state": "finding", "reason": "candidate redlines available"}


def _export_legacy_coarse_pivots(article, output):
    """Withhold frozen coarse candidates that predate compatible evidence receipts."""
    return _unavailable_export(
        output,
        article,
        "unavailable",
        "legacy coarse pivot lacks compatible evidence receipt",
    )


def _load_confirmation(article, findings_dir=None):
    if findings_dir is None:
        return drift.load_confirmation(article)
    path = pathlib.Path(findings_dir) / drift.confirmation_name(article)
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def export_pivots(article, findings_dir=None, database=None):
    output = DATA / f"{config.slugify(article)}.pivots.json"
    confirmation = _load_confirmation(article, findings_dir)
    if confirmation:
        return _export_candidate_pivots(article, confirmation, output, database)
    return _export_legacy_coarse_pivots(article, output)


if __name__ == "__main__":
    print("exporting L3 pivot timelines + authored diffs (WikiWho)...")
    have_pivots = set()
    rewrite_status = {}
    article_sources = published_article_sources()
    articles = list(article_sources)
    print(f"  export roster: {len(articles)} article(s) from profiles and confirmations")
    for a in articles:
        status = export_pivots(a, **article_sources[a])
        rewrite_status[a] = status
        if status["state"] == "finding":
            have_pivots.add(a)
    DATA.mkdir(parents=True, exist_ok=True)
    (DATA / "rewrite_status.json").write_text(
        json.dumps(rewrite_status, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("exporting L3 simple diff fallback...")
    for a, b in DIFF.items():
        if a not in have_pivots:
            export_diff(a, b)
    print("done -> viewer/data/")
