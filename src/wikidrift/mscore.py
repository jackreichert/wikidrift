"""Yasseri mutual-revert M-score — controversy corroborator (promoted from spike 013).

Metadata-only conflict signal from the REVERT GRAPH (no text, no WikiWho) — sits beside the
pre-ranker (`prerank`). Identity-reverts via content hash (sha1); `M = E · Σ min(N_i,N_j)` over
mutual-revert pairs.

HONEST ROLE (spike 013 findings — a corroborator, NOT a flag):
  * Use the REFINED M (registered editors + sustained ≥2 mutual reverts) — raw M over-rates
    vandalism magnets ~20×.
  * It does NOT separate benign from malicious change: Climate change dominates (genuinely
    edit-warred) though its recent rewrite was benign. Controversy ≠ malice.
  * Its value is corroboration + the LOW end: high-M = contested; **low/zero M on a flagged
    article (Nakba, KL Warschau) = a route-to-L5 signal** (consensus/quiet distortion — the mode
    a controversy measure structurally can't judge).
"""
import json
from collections import Counter, defaultdict

from . import config

_S = config.session()
_CACHE = config.DATA_DIR / "mscore"


def history(title, force=False):
    """All revisions oldest→newest with sha1 (cached to DATA_DIR/mscore/<slug>.json).

    The cache has no natural expiry (an article keeps being edited), so a stale cache would report a
    month-old M-score with no warning. `force=True` bypasses the read and re-fetches — use it whenever a
    fresh controversy read matters (the cache is still written, so subsequent reads stay fast)."""
    _CACHE.mkdir(parents=True, exist_ok=True)
    cache = _CACHE / f"{config.slugify(title)}.json"
    if cache.exists() and not force:
        return json.loads(cache.read_text(encoding="utf-8"))
    revs, cont = [], None
    while True:
        p = {"action": "query", "format": "json", "formatversion": "2", "prop": "revisions",
             "titles": title, "rvprop": "ids|timestamp|user|sha1", "rvlimit": "max", "rvdir": "newer"}
        if cont:
            p["rvcontinue"] = cont
        d = _S.get(config.ACTION, params=p, timeout=60).json()
        revs += d["query"]["pages"][0].get("revisions", [])
        cont = d.get("continue", {}).get("rvcontinue")
        if not cont:
            break
    cache.write_text(json.dumps(revs, ensure_ascii=False), encoding="utf-8")
    return revs


def mscore(revs, registered_only=True, min_each=2):
    """M = E · Σ min(N_i,N_j) over mutual-revert pairs. Defaults = the REFINED score
    (registered editors + sustained ≥2 mutual reverts) that suppresses the vandalism confound."""
    revs = [r for r in revs if "user" in r and "sha1" in r and not r["user"].lower().endswith("bot")]
    if registered_only:
        revs = [r for r in revs if not config.ANON_IP_RE.match(r["user"])]
    edits = Counter(r["user"] for r in revs)
    first, pairs = {}, defaultdict(int)
    for k, r in enumerate(revs):
        sha, u = r["sha1"], r["user"]
        if sha in first and first[sha] < k - 1:
            for ru in {revs[m]["user"] for m in range(first[sha] + 1, k)}:
                if ru != u:
                    pairs[(u, ru)] += 1
        first.setdefault(sha, k)
    mutual = [(a, b) for (a, b) in pairs if a < b and (b, a) in pairs
              and pairs[(a, b)] >= min_each and pairs[(b, a)] >= min_each]
    warriors = {e for pair in mutual for e in pair}
    M = len(warriors) * sum(min(edits[a], edits[b]) for a, b in mutual)
    return {"revs": len(revs), "mutual_pairs": len(mutual), "M": M,
            "M_per_rev": round(M / len(revs), 2) if revs else 0.0}


def run(articles, force=False):
    """Print raw vs refined M for each article; return {article: {raw, refined}}.
    force=True re-fetches revision history instead of trusting a possibly-stale cache."""
    print(f"{'article':<32} {'revs':>6} {'M(raw)':>12} {'M(refined)':>12} {'refined/rev':>11}")
    print("-" * 76)
    out = {}
    for a in articles:
        revs = history(a, force=force)
        raw = mscore(revs, registered_only=False, min_each=1)
        refined = mscore(revs)
        rpr = round(refined["M"] / raw["revs"], 2) if raw["revs"] else 0.0
        out[a] = {"raw": raw, "refined": refined, "refined_per_rev": rpr}
        print(f"{a[:32]:<32} {raw['revs']:>6} {raw['M']:>12,} {refined['M']:>12,} {rpr:>11}")
    print("\nrefined = registered editors + sustained mutual reverts (≥2). Corroborator, not a flag;"
          "\nlow/zero M on a flagged article ⇒ not fought-over ⇒ route to L5.")
    merged = config.load_findings("mscore.json")
    merged.update(out)
    config.write_findings("mscore.json", merged)
    return out
