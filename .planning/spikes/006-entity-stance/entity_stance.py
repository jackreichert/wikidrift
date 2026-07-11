"""Spike 006 (L2) — entity-stance / framing over time: catch reframing by ADDITION or removal.

Same thesis as the drift engine: a stable curve with a pivot. Here the curve is how the article
*talks about* things over its timeline, not how much text survives. Reframing (e.g. shifting an
entity from critical → sympathetic) shows as a shift in framing-term usage and/or focal-entity
sentiment — whether achieved by adding sympathetic text OR removing critical text.

PROTOTYPE scope + honesty:
  - Framing-term trajectories (per 1,000 words) are transparent and interpretable; the tool does NOT
    label terms good/bad — it shows the trajectory; the researcher interprets.
  - Focal-entity sentiment uses VADER — a GENERIC lexicon, a weak proxy for political stance on
    encyclopedic prose. Placeholder for a real LLM stance classifier (the production L2).
  - A pivot is a SIGNAL, not proof: a real-world event can legitimately shift framing. Flag for a
    researcher to follow up (exactly like the drift detector).

Reads the robust snapshot revisions (rsnap) written by spike 005; fetches wikitext at each via the
Action API; strips to plaintext with mwparserfromhell.

Usage: uv run python entity_stance.py "Zionism"
"""
import sys
import re
import pathlib
import statistics
import requests
import duckdb
import mwparserfromhell
from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer

UA = "gh-wiki-spike/0.1 (awesome@rpophesagr.com)"
ACTION = "https://en.wikipedia.org/w/api.php"
DB = pathlib.Path(__file__).resolve().parents[1] / "data" / "provenance.duckdb"
S = requests.Session(); S.headers.update({"User-Agent": UA})
VADER = SentimentIntensityAnalyzer()

# Transparent, editable framing-term set for the Israel/Palestine domain. NOT labeled good/bad —
# the trajectory is the signal; the researcher interprets valence.
FRAMING_TERMS = [
    "colonial", "colonialism", "settler", "apartheid", "ethnic cleansing", "indigenous",
    "racist", "racism", "occupation", "self-determination", "liberation", "nationalist",
]
FOCAL = "Zionism|Zionist"   # sentences mentioning the focal entity → sentiment proxy


def snap_revs(con, article):
    return con.execute("SELECT DISTINCT snap_date, snap_rev FROM rsnap WHERE article=? ORDER BY snap_date",
                       [article]).fetchall()


def fetch_wikitext(revids):
    out = {}
    for i in range(0, len(revids), 10):
        batch = revids[i:i+10]
        p = {"action": "query", "format": "json", "formatversion": "2", "prop": "revisions",
             "revids": "|".join(str(r) for r in batch), "rvprop": "ids|content", "rvslots": "main", "maxlag": "5"}
        d = S.get(ACTION, params=p, timeout=120).json()
        for pg in d.get("query", {}).get("pages", []):
            for rv in pg.get("revisions", []):
                try:
                    out[int(rv["revid"])] = rv["slots"]["main"]["content"]
                except (KeyError, TypeError):
                    pass
    return out


def analyse(text):
    plain = mwparserfromhell.parse(text).strip_code()
    words = re.findall(r"\w+", plain.lower())
    nwords = max(len(words), 1)
    joined = " " + " ".join(words) + " "
    freqs = {}
    for term in FRAMING_TERMS:
        c = joined.count(" " + term.replace(" ", " ") + " ") if " " not in term else plain.lower().count(term)
        freqs[term] = 1000.0 * c / nwords
    # focal-entity sentiment (VADER — crude proxy)
    sents = re.split(r"(?<=[.!?])\s+", plain)
    focal = [s for s in sents if re.search(FOCAL, s, re.I)]
    sent = statistics.mean([VADER.polarity_scores(s)["compound"] for s in focal]) if focal else 0.0
    return nwords, freqs, len(focal), sent


def main(article):
    con = duckdb.connect(str(DB), read_only=True)
    snaps = snap_revs(con, article)
    con.close()
    if not snaps:
        print(f"No rsnap snapshots for {article} — run spike 005 analyze.py first."); return
    texts = fetch_wikitext([r for _, r in snaps])
    rows = []
    for date, rev in snaps:
        if rev not in texts:
            continue
        nwords, freqs, nfocal, sent = analyse(texts[rev])
        rows.append((date, nwords, freqs, nfocal, sent))

    print(f"=== ENTITY-STANCE / FRAMING over time — {article} ===")
    print(f"(framing terms per 1,000 words; focal sentiment = VADER on '{FOCAL}' sentences — crude proxy)\n")
    # framing-term trajectory table (show a subset of dates to stay readable)
    show = rows[::2] if len(rows) > 14 else rows
    hdr = "date       | words  | " + " ".join(f"{t[:8]:>8}" for t in FRAMING_TERMS[:6])
    print(hdr); print("-"*len(hdr))
    for date, nwords, freqs, nfocal, sent in show:
        print(f"{date} | {nwords:>6,} | " + " ".join(f"{freqs[t]:>8.2f}" for t in FRAMING_TERMS[:6]))
    print()
    hdr2 = "date       | " + " ".join(f"{t[:8]:>8}" for t in FRAMING_TERMS[6:]) + " | focalSent"
    print(hdr2); print("-"*len(hdr2))
    for date, nwords, freqs, nfocal, sent in show:
        print(f"{date} | " + " ".join(f"{freqs[t]:>8.2f}" for t in FRAMING_TERMS[6:]) + f" | {sent:>+8.3f}")

    # simple pivot on each term: biggest jump between consecutive shown snapshots
    print("\n── framing pivots (largest single-interval jump per term; SIGNAL, not proof) ──")
    for term in FRAMING_TERMS:
        series = [(d, f[term]) for d, _, f, _, _ in rows]
        jumps = [(series[i+1][0], series[i+1][1]-series[i][1]) for i in range(len(series)-1)]
        if not jumps:
            continue
        d_at, jump = max(jumps, key=lambda x: abs(x[1]))
        start = statistics.mean([v for _, v in series[:3]]) if len(series) >= 3 else series[0][1]
        end = statistics.mean([v for _, v in series[-3:]]) if len(series) >= 3 else series[-1][1]
        arrow = "↑" if end > start else ("↓" if end < start else "→")
        if abs(jump) >= 0.15 or abs(end-start) >= 0.2:
            print(f"  {term:<16} {start:>5.2f} → {end:>5.2f} /1k  {arrow}   biggest jump @ {d_at} ({jump:+.2f})")
    # focal sentiment trajectory
    ss = [(d, s) for d, _, _, _, s in rows]
    print(f"\n  focal-entity sentiment: {ss[0][1]:+.3f} ({ss[0][0]}) → {ss[-1][1]:+.3f} ({ss[-1][0]})  [VADER, crude]")
    print("\nNote: a shift is a LEAD for a researcher — real-world events can legitimately reframe an entity.")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "Zionism")
