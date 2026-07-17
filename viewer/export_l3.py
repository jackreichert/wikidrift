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
from wikidrift import config, drift, provenance  # noqa: E402
from wikidrift.corpus import Corpus  # noqa: E402
from wikidrift.l5_crosslingual import fetch_asof  # noqa: E402
from wikidrift.stance import prose_at  # noqa: E402

DATA = pathlib.Path(__file__).resolve().parent / "data"
BLAME_TOKENS = 1500                        # blame the lead only (full-article is a v2 paginated view)

# Articles to build a pivot-timeline + authored per-pivot diff for (need L1 episodes).
# Expand as cache allows; the site shows an honest "not exported" note when missing.
PIVOTS = [
    "Zionism", "Nakba", "Warsaw concentration camp",
    "Palestine", "UNRWA", "Bar Kokhba Revolt", "Anti-Zionism",
    "Israeli–Palestinian conflict", "History of Zionism",
]
# Simple before/after diff fallback (articles with no L1 pivot). None = auto (2yr before onset).
DIFF = {"Warsaw concentration camp": "2018-06-01"}
BLAME = ["Zionism"]


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


def export_pivots(article):
    output = DATA / f"{config.slugify(article)}.pivots.json"
    con = duckdb.connect(str(config.DB), read_only=True)
    try:
        corpus = Corpus(con)
        eps = drift.verdict_dict(con, article).get("episodes", [])
        snaprev = dict(corpus.snapshots(article))
        authors = corpus.revision_editor(article)
    finally:
        con.close()
    pivs = []
    for e in sorted(eps, key=lambda e: e["start"]):
        br, ar = snaprev.get(e["start"]), snaprev.get(e["end"])
        if not br or not ar:
            continue
        bt, at = provenance.tokens_at(article, br), provenance.tokens_at(article, ar)
        if not bt or not at:
            continue
        before_text = prose_at(br)
        after_text = prose_at(ar)
        pivs.append({"start": e["start"], "end": e["end"], "peak_pct": e["peak_pct"],
                     "pwr_mass": e["pwr_mass"], "before_rev": br, "after_rev": ar,
                 "status": "candidate", "metric": "persistence_weighted_loss",
                     "before_text": before_text, "after_text": after_text,
                     "authors_before": _word_authors(bt, authors), "authors_after": _word_authors(at, authors)})
    if not pivs:
        output.unlink(missing_ok=True)
        print(f"  pivots {article}: none (L1=HEALTHY) — will use simple diff")
        return False
    DATA.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps({"article": article, "pivots": pivs}, ensure_ascii=False), encoding="utf-8")
    print(f"  pivots {article}: {len(pivs)} pivot(s)")
    return True


if __name__ == "__main__":
    print("exporting L3 pivot timelines + authored diffs (WikiWho)...")
    have_pivots = set()
    for a in PIVOTS:
        if export_pivots(a):
            have_pivots.add(a)
    print("exporting L3 simple diff fallback...")
    for a, b in DIFF.items():
        if a not in have_pivots:
            export_diff(a, b)
    print("done -> viewer/data/")
