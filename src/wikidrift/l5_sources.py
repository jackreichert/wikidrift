"""L5 instrument #3b — citation-source composition over time (REFERENCE-AGNOSTIC).

The other #3 idea — "does the article diverge from scholarly consensus?" — anchors to an external authority
that is itself disputed on contested topics, so it can't be a neutral oracle. #3b deliberately anchors to
NOTHING external. It characterizes an article's OWN reference list — *what* it cites and *how that mix drifts*,
especially across the L1 pivot — from two structural, **editor-declared** signals in the wikitext:

  1. cite-template TYPE mix — {{cite journal | news | book | web | report | press release | …}} and
     {{citation}}. This is the editors' own classification of their sources; we just read it.
  2. citation DOMAINS (+ coarse TLD buckets), with per-domain GROWTH. A domain going 0 → N across a pivot is
     the "source injection" pattern (e.g. an advocacy site added many-fold — the kind of thing the ADL report
     flagged) — surfaced as a fact about composition, NOT labelled good/bad.

Descriptive, never a verdict. It does not decide any source is reliable or biased (that judgement is exactly
what the neutral-mechanism discipline forbids); it makes the composition and its drift legible, and the reader
judges. Offline but for cheap Action-API content fetches (no WikiWho, no LLM).
"""
import re
import time

import duckdb

from . import config, drift
from .corpus import Corpus

_S = config.session()


def _source_domains(raw):
    """Citation domains from wikitext external links, with Wayback wrappers unwrapped to the real source.
    Thin wrapper over the shared config.citation_domains so the URL/Wayback parsing lives in one place."""
    return config.citation_domains(raw, unwrap_wayback=True)

# The editors' own structural source classification, read from cite templates. `citation` is generic.
_CITE_RE = re.compile(r"\{\{\s*(cite\s+[a-z][a-z ]*?|citation)\s*(?:\||\}\})", re.I)
_REF_RE = re.compile(r"<ref\b", re.I)
# Buckets a template type into a coarse, neutral class (structural, not a quality judgement).
_TYPE_BUCKET = {
    "cite journal": "journal", "cite book": "book", "cite news": "news", "cite web": "web",
    "cite report": "report", "cite press release": "press", "cite magazine": "news",
    "cite conference": "journal", "cite thesis": "journal", "cite encyclopedia": "book",
    "citation": "generic",
}


def wikitext_at(rev_id):
    """Raw wikitext of a specific revision (Action API). No strip_code — we need the citation markup.
    Returns "" if the revision can't be fetched/parsed after retries."""
    try:
        d = config.get_json_retrying(_S, config.ACTION, params={"action": "query", "format": "json",
            "formatversion": "2", "prop": "revisions", "revids": rev_id, "rvprop": "content",
            "rvslots": "main"}, timeout=30)
        return d["query"]["pages"][0]["revisions"][0]["slots"]["main"]["content"]
    except Exception:                                   # noqa: BLE001 — unavailable/malformed → caller skips
        return ""


def _tld_bucket(domain):
    """Coarse, neutral TLD class. .edu/.ac.* and .gov/.mil/.int are unambiguous; else the raw TLD."""
    if domain.endswith(".edu") or ".ac." in domain or domain.endswith(".ac.uk"):
        return "edu"
    if domain.endswith(".gov") or domain.endswith(".mil") or domain.endswith(".int") or domain.endswith(".un.org"):
        return "gov/int"
    tld = domain.rsplit(".", 1)[-1] if "." in domain else domain
    return {"org": "org", "com": "com", "net": "net"}.get(tld, tld)


def composition(raw):
    """Structural source composition of one revision's wikitext: cite-type mix, ref count, domains, TLDs."""
    types = {}
    for m in _CITE_RE.findall(raw):
        key = re.sub(r"\s+", " ", m.strip().lower())
        bucket = _TYPE_BUCKET.get(key, "other")
        types[bucket] = types.get(bucket, 0) + 1
    doms = _source_domains(raw)
    tld = {}
    for dom, n in doms.items():
        b = _tld_bucket(dom)
        tld[b] = tld.get(b, 0) + n
    return {"refs": len(_REF_RE.findall(raw)), "cite_types": types,
            "n_domains": len(doms), "domains": doms, "tld": tld}


def _snaps(con, article):
    return Corpus(con).snapshots(article)


def _deltas(early, late):
    """Per-domain citation-count change between two composition snapshots. Returns (added, dropped):
    `added` = domains that grew (0→N = a newly-relied-on source), `dropped` = domains that shrank
    (N→0 = a source the article moved away from). Both are the 'from → to', surfaced as fact, not judged."""
    keys = set(early) | set(late)
    rows = [(d, late.get(d, 0) - early.get(d, 0), early.get(d, 0), late.get(d, 0)) for d in keys]
    added = sorted([r for r in rows if r[1] > 0], key=lambda r: -r[1])
    dropped = sorted([r for r in rows if r[1] < 0], key=lambda r: r[1])
    return added, dropped


def sources_over_time(article, max_snaps=12, persist=True):
    """Citation-source composition trajectory for one article + growth (first→last and across the L1 pivot).
    Prints a report and writes findings/<slug>.sources.json. Reference-agnostic; no LLM."""
    con = duckdb.connect(str(config.DB), read_only=True)
    snaps = _snaps(con, article)
    pivot = None
    try:                                            # offline L1 pivot (top episode start), if any
        v = drift.verdict_dict(con, article)
        if v.get("episodes"):
            pivot = v["episodes"][0]["start"]
    except Exception:
        pass
    con.close()
    if not snaps:
        print(f"  no snapshots for {article}"); return None
    if max_snaps and len(snaps) > max_snaps:        # even-sample to bound Action-API fetches
        step = len(snaps) / max_snaps
        idx = sorted({int(i * step) for i in range(max_snaps)} | {len(snaps) - 1})
        snaps = [snaps[i] for i in idx]

    traj = []
    for sd, sr in snaps:
        traj.append((sd, sr, composition(wikitext_at(sr))))
        time.sleep(0.2)                             # polite to the Action API
    last_row = traj[-1]
    now = last_row[2]

    # Anchor to the L1 pivot: compare the state just BEFORE the pivot to now — "what the sourcing changed
    # from → to, across the pivot". No pivot ⇒ fall back to whole-history first→now (stated as such).
    if pivot:
        pre_row = max((r for r in traj if r[0] <= pivot), default=traj[0], key=lambda r: r[0])
        span = f"{pre_row[0]} → {last_row[0]}  (across the L1 pivot ~{pivot})"
    else:
        pre_row = traj[0]
        span = f"{pre_row[0]} → {last_row[0]}  (no L1 pivot — whole history)"
    pre = pre_row[2]
    added, dropped = _deltas(pre["domains"], now["domains"])

    def _mix(c):
        typed = {k: v for k, v in c["cite_types"].items() if k in ("journal", "news", "book", "web", "report", "press")}
        tot = sum(typed.values()) or 1
        return {k: round(100 * v / tot) for k, v in sorted(typed.items(), key=lambda x: -x[1])}

    print(f"=== L5 #3b — citation-source change, {article} ===")
    print(f"  {span}")
    print(f"  refs {pre['refs']} → {now['refs']}    domains {pre['n_domains']} → {now['n_domains']}")
    print(f"  cite-type mix (% of typed cites):  {_mix(pre)}  →  {_mix(now)}")
    print(f"  sources ADDED / grown (from → to):")
    for d, dl, e, l in added[:10]:
        print(f"    {e:>3} → {l:<3}  {d}")
    print(f"  sources DROPPED / reduced (from → to):")
    for d, dl, e, l in (dropped[:10] or [("(none)", 0, 0, 0)]):
        print(f"    {e:>3} → {l:<3}  {d}")
    print("  (the pivot's citation-source change, shown as-is — no source is rated; the reader judges.)")

    out = {
        "article": article, "pivot": pivot, "span": span,
        "before": {"date": pre_row[0], "refs": pre["refs"], "n_domains": pre["n_domains"],
                   "cite_mix": _mix(pre), "domains": pre["domains"]},
        "after": {"date": last_row[0], "refs": now["refs"], "n_domains": now["n_domains"],
                  "cite_mix": _mix(now), "domains": now["domains"]},
        "added": [{"domain": d, "from": e, "to": l} for d, dl, e, l in added],
        "dropped": [{"domain": d, "from": e, "to": l} for d, dl, e, l in dropped],
        "note": "Reference-agnostic: the article's OWN citation-source change across its L1 pivot, from → to. "
                "Structural/editor-declared signals only. No source is rated reliable/biased. A LEAD.",
    }
    if persist:
        slug = config.slugify(article)
        config.write_findings(f"{slug}.sources.json", out)
        print(f"  wrote findings/{slug}.sources.json")
    return out
