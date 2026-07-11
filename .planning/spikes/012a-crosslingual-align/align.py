"""Spike 012a — cross-lingual article alignment + prose fetch (L5 instrument #1).

Given an English article title and a language set, resolve the SAME article across
Wikipedia language editions via Wikidata sitelinks, fetch each edition's wikitext
(current, or as-of a timestamp for pivot-relative comparison), strip to prose, and
save artifacts — the prose files plus a "receipts" JSON (Q-id, per-lang titles,
revids, timestamps, sizes) that feed 012b (native stance) and 012c (divergence).

Read-only, polite User-Agent. No API key needed (fetching only).

Run:
    .venv/bin/python .planning/spikes/012a-crosslingual-align/align.py                # whole slate, current
    .venv/bin/python .planning/spikes/012a-crosslingual-align/align.py "Nakba" en,he,ar
    .venv/bin/python .planning/spikes/012a-crosslingual-align/align.py "Zionism" en,he,ar 2023-10-06T00:00:00Z
"""
import json
import pathlib
import sys
import time

import requests
import mwparserfromhell

UA = "gh-wiki/0.1 (awesome@rpophesagr.com; wikipedia-drift-detector L5 crosslingual spike)"
WIKIDATA = "https://www.wikidata.org/w/api.php"
OUT = pathlib.Path(__file__).resolve().parent / "out"

# Topic-appropriate language sets — the researcher picks the relevant editions.
# I-P cases use en/he/ar; the Polish-Holocaust case (KL Warschau) uses en/pl/de.
SLATE = {
    "Nakba": ["en", "he", "ar"],                          # born-framed — L5 should light up
    "Zionism": ["en", "he", "ar"],                        # internal layers already succeed here
    "Photosynthesis": ["en", "he", "ar"],                 # neutral control — editions should agree
    "Warsaw concentration camp": ["en", "pl", "de"],      # KL Warschau — certified L5-gap miss
}

_S = requests.Session()
_S.headers.update({"User-Agent": UA})


def qid_and_sitelinks(en_title):
    """English title -> (Q-id, {lang: title}) via Wikidata sitelinks (the authoritative map)."""
    r = _S.get(WIKIDATA, params={
        "action": "wbgetentities", "format": "json", "sites": "enwiki",
        "titles": en_title, "props": "sitelinks", "normalize": 1}, timeout=30).json()
    ent = next(iter(r["entities"].values()))
    if "missing" in ent:
        raise LookupError(f"no Wikidata item for enwiki title {en_title!r}")
    qid = ent["id"]
    links = {}
    for site, info in ent.get("sitelinks", {}).items():
        if site.endswith("wiki") and not site.startswith(
                ("commons", "meta", "species", "wikidata", "sources", "quote", "voyage", "news", "books", "versity")):
            links[site[:-4]] = info["title"]         # 'enwiki' -> 'en'
    return qid, links


def endpoint(lang):
    return f"https://{lang}.wikipedia.org/w/api.php"


def prose_asof(lang, title, ts=None):
    """Wikitext for `title` on {lang}.wikipedia — current, or the last revision <= ts.
    Returns (revid, timestamp, prose) or (None, None, "") if absent."""
    params = {"action": "query", "format": "json", "formatversion": "2", "prop": "revisions",
              "titles": title, "rvprop": "ids|timestamp|content", "rvslots": "main", "redirects": 1}
    if ts:
        params["rvstart"] = ts      # start from this timestamp, going backwards -> first hit is "as of ts"
        params["rvlimit"] = 1
    r = _S.get(endpoint(lang), params=params, timeout=30).json()
    try:
        page = r["query"]["pages"][0]
        rev = page["revisions"][0]
        content = rev["slots"]["main"]["content"]
    except (KeyError, IndexError):
        return None, None, ""
    prose = mwparserfromhell.parse(content).strip_code()
    return rev["revid"], rev["timestamp"], prose


def align(en_title, langs, ts=None):
    qid, links = qid_and_sitelinks(en_title)
    OUT.mkdir(exist_ok=True)
    slug = en_title.replace(" ", "_")
    tag = f".asof-{ts[:10]}" if ts else ""
    receipts = {"article": en_title, "qid": qid, "asof": ts, "editions": {}}
    for lang in langs:
        title = links.get(lang)
        if not title:
            print(f"  {lang}: (no sitelink)")
            receipts["editions"][lang] = {"present": False}
            continue
        revid, rts, prose = prose_asof(lang, title, ts)
        if not prose:
            print(f"  {lang}: {title!r} (no revision as of {ts})")
            receipts["editions"][lang] = {"present": False, "title": title}
            continue
        fn = OUT / f"{slug}.{lang}{tag}.txt"
        fn.write_text(prose, encoding="utf-8")
        receipts["editions"][lang] = {"present": True, "title": title, "revid": revid,
                                      "timestamp": rts, "prose_chars": len(prose), "prose_file": fn.name}
        print(f"  {lang}: {title!r}  rev {revid} @ {rts}  {len(prose):,} chars -> {fn.name}")
        time.sleep(0.2)
    rfn = OUT / f"{slug}{tag}.receipts.json"
    rfn.write_text(json.dumps(receipts, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  receipts -> {rfn.name}  (Q-id {qid})")
    return receipts


if __name__ == "__main__":
    if len(sys.argv) > 1:
        title = sys.argv[1]
        langs = sys.argv[2].split(",") if len(sys.argv) > 2 else SLATE.get(title, ["en", "he", "ar"])
        ts = sys.argv[3] if len(sys.argv) > 3 else None
        print(f"=== align: {title} ({','.join(langs)}){' @ ' + ts if ts else ''} ===")
        align(title, langs, ts)
    else:
        for title, langs in SLATE.items():
            print(f"=== align: {title} ({','.join(langs)}) ===")
            try:
                align(title, langs)
            except LookupError as e:
                print(f"  ERROR: {e}")
            print()
