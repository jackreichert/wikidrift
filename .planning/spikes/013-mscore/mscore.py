"""Spike 013 — Yasseri mutual-revert M-score (controversy corroborator).

Prior-art strategy #3 (unbuilt) and the missing "persisted-against-reverts" clause of the
§10 conjunction. A metadata-only conflict signal from the REVERT GRAPH — no article text,
no WikiWho — so it belongs beside the pre-ranker (spike 008).

Method (Yasseri et al., PLOS ONE 2012):
  1. Identity-revert = a revision whose content hash (sha1) equals an earlier revision's
     (the article was restored to a prior state). The reverter "reverts" the editors of the
     intervening revisions.
  2. Mutual-revert pair (i, j): i reverted j AND j reverted i.
  3. M = E · Σ_{mutual pairs} min(N_i, N_j)
     where N = an editor's total edits on the article, E = # distinct mutual reverters.
  min() stops one prolific edit-warrior from dominating; E scales by how many are fighting.

Why it matters here: it separates FOUGHT-OVER rewrites (edit wars → high M) from
SMOOTH-CONSENSUS ones (low M) — directly attacking the base-rate problem (Climate's benign
2020-21 restructuring out-pivoted Zionism on PWR; M should NOT rank it as controversial).
Caveat: majority-consensus capture is smooth by design → low M (that's L5's job, not M's).

No API key. Run:  .venv/bin/python .planning/spikes/013-mscore/mscore.py
"""
import json
import pathlib
import re
import sys
from collections import Counter, defaultdict

import requests

# An unregistered editor = an IPv4/IPv6 username. Reverts involving anons are disproportionately
# vandalism↔revert, not content controversy, so the "refined" M excludes them.
IP = re.compile(r"^(\d{1,3}\.){3}\d{1,3}$|^[0-9A-Fa-f:]+:[0-9A-Fa-f:]+$")

OUT = pathlib.Path(__file__).resolve().parent / "out"
UA = "gh-wiki/0.1 (awesome@rpophesagr.com; wikipedia-drift-detector M-score spike)"
ACTION = "https://en.wikipedia.org/w/api.php"
_S = requests.Session()
_S.headers.update({"User-Agent": UA})

ARTICLES = ["Zionism", "Nakba", "Warsaw concentration camp", "Israeli–Palestinian conflict",
            "Photosynthesis", "Climate change", "Water"]


def history(title):
    """All revisions oldest→newest with sha1 (cached to out/<slug>.history.json)."""
    OUT.mkdir(exist_ok=True)
    cache = OUT / f"{title.replace(' ', '_')}.history.json"
    if cache.exists():
        return json.loads(cache.read_text(encoding="utf-8"))
    revs, cont = [], None
    while True:
        p = {"action": "query", "format": "json", "formatversion": "2", "prop": "revisions",
             "titles": title, "rvprop": "ids|timestamp|user|sha1", "rvlimit": "max",
             "rvdir": "newer"}
        if cont:
            p["rvcontinue"] = cont
        d = _S.get(ACTION, params=p, timeout=60).json()
        page = d["query"]["pages"][0]
        revs += page.get("revisions", [])
        cont = d.get("continue", {}).get("rvcontinue")
        if not cont:
            break
    cache.write_text(json.dumps(revs, ensure_ascii=False), encoding="utf-8")
    return revs


def is_bot(user):
    return user.lower().endswith("bot")


def mscore(revs, registered_only=False, min_each=1):
    """M = E · Σ min(N_i,N_j) over mutual-revert pairs. `registered_only` drops anon (IP)
    editors; `min_each` requires that many reverts in EACH direction (sustained warring) —
    together the 'refined' M that suppresses the vandalism↔revert confound."""
    revs = [r for r in revs if "user" in r and "sha1" in r and not is_bot(r["user"])]
    if registered_only:
        revs = [r for r in revs if not IP.match(r["user"])]
    edits = Counter(r["user"] for r in revs)
    first = {}                                          # sha1 -> earliest index
    pairs = defaultdict(int)                            # (reverter, reverted) -> count
    for k, r in enumerate(revs):
        sha, u = r["sha1"], r["user"]
        if sha in first and first[sha] < k - 1:        # restored an earlier state, ≥1 edit undone
            reverted = {revs[m]["user"] for m in range(first[sha] + 1, k)}
            for ru in reverted:
                if ru != u:
                    pairs[(u, ru)] += 1
        first.setdefault(sha, k)
    mutual = [(a, b) for (a, b) in pairs if a < b and (b, a) in pairs
              and pairs[(a, b)] >= min_each and pairs[(b, a)] >= min_each]
    warriors = {e for pair in mutual for e in pair}
    weight = sum(min(edits[a], edits[b]) for a, b in mutual)
    M = len(warriors) * weight
    return {"revs": len(revs), "editors": len(edits), "mutual_pairs": len(mutual),
            "mutual_reverters": len(warriors), "weight": weight, "M": M,
            "M_per_rev": round(M / len(revs), 2) if revs else 0.0}


if __name__ == "__main__":
    print(f"{'article':<32} {'revs':>6} {'M(raw)':>12} {'M(refined)':>12} {'refined/rev':>11}")
    print("-" * 76)
    results = {}
    for a in ARTICLES:
        try:
            revs = history(a)
            raw = mscore(revs)
            refined = mscore(revs, registered_only=True, min_each=2)   # anon-free, sustained warring
        except Exception as e:
            print(f"{a:<32} ERROR: {e}")
            continue
        rpr = round(refined["M"] / raw["revs"], 2) if raw["revs"] else 0.0
        results[a] = {"raw": raw, "refined": refined, "refined_per_rev": rpr}
        print(f"{a[:32]:<32} {raw['revs']:>6} {raw['M']:>12,} {refined['M']:>12,} {rpr:>11}")
    (OUT / "mscore.json").write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print("\nrefined = registered editors only + sustained mutual reverts (≥2 each way)")
    print("-> out/mscore.json")
