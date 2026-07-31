"""L5 cross-language stance comparison (promoted from spikes 012a/b/c).

Catches *framing* capture the internal engine (L1 change-detector, L2 temporal stance) is blind
to, by comparing the SAME article across language editions. Reuses the L2 NPOV classifier
(`stance.classify`) on each edition's native prose — no translation — and diffs, in two modes:

  static          — how far editions disagree now (the born-biased fallback; e.g. Nakba).
  pivot-relative  — does English peel away from the he/ar consensus ACROSS the L1 pivot?
                    (drift.verdict_dict supplies the pivot; capture vs legitimate event.)

Scope (spike 012b): this instrument catches FRAMING capture (Israel-Palestine); factual/numerical
distortion (KL Warschau) is blind to stance and belongs to `l5_factcheck` (instrument #2).
Output makes disagreement LEGIBLE — a lead, never a neutral-truth verdict.

Needs an LLM key (default Anthropic; pick a cheaper/local backend via --provider/--model/--base-url or
WIKIDRIFT_LLM_* env — see llm.py).
"""
import duckdb
import mwparserfromhell

from . import config, drift
from .corpus import Corpus
from .stance import (STANCE_PROMPT_VERSION, STANCE_SCHEMA_VERSION, STANCE_VAL, classify,
                     default_entities, focal_passage)

_S = config.session()
MAX_CHARS = 6000
DEFAULT_COMPARE_LANGS = 3
MAJOR_LANG_PRIORITY = [
    "en", "de", "fr", "es", "ru", "ja", "zh", "it", "pt", "pl",
    "nl", "sv", "uk", "ar", "he", "tr", "fa", "cs", "ko", "id",
]

# Topic-appropriate default language sets and focal entities (transparent, researcher-editable).
SLATE = {
    "Nakba": ["en", "he", "ar"],
    "Zionism": ["en", "he", "ar"],
    "Photosynthesis": ["en", "he", "ar"],
    "Warsaw concentration camp": ["en", "pl", "de"],
    # --- expansion slate (Session 07; langsets verified vs Wikidata in the run-list) ---
    "Hamas": ["en", "he", "ar"],                                    # framing (#1)
    "Israeli–Palestinian conflict": ["en", "he", "ar"],            # framing (#1)
    "Palestinian political violence": ["en", "he", "ar"],          # framing (#1); L1 HEALTHY → pivot fallback
    "Gaza war": ["en", "he", "ar"],                                # fact (#2) + framing; scope caveat
    "Jedwabne pogrom": ["en", "pl", "de"],                         # fact (#2) — count/attribution dispute
    "Naliboki massacre": ["en", "pl"],                             # fact (#2) — de edition missing
    "Rescue of Jews by Poles during the Holocaust": ["en", "pl"],  # fact (#2) — de edition missing
    # --- Session 08: L4-surfaced + roster gap-fill (charged-relevant completion set) ---
    "Palestine": ["en", "he", "ar"],                               # confirmed pivot + sustained L2 shift
    "UNRWA": ["en", "he", "ar"],                                   # confirmed pivot + sustained L2 shift
    "Anti-Zionism": ["en", "he", "ar"],                            # roster PIVOT, never got L5 (framing #1)
    "Collaboration in German-occupied Poland": ["en", "pl", "de"], # roster addition→L2 (fact #2 — collaboration scale)
    "History of Zionism": ["en", "he", "ar"],                      # Zionism-adjacent
    "Genetic studies of Jews": ["en", "he"],                       # indigeneity-framing / fact (#2)
    "Racial conceptions of Jewish identity in Zionism": ["en", "he"],  # framing (#1)
    "Bar Kokhba Revolt": ["en", "he"],                             # confirmed pivot (case study); fact #2 (Syria Palaestina)
    "Gaza genocide": ["en", "he", "ar"],                           # born-in-contested → L5's home (small; static-led)
}
# I-P pivot fallback when L1 reads HEALTHY (addition-side growth, no removal pivot). Anchored to Oct-7-2023 —
# the natural I-P reframe boundary — for the born-in-contested / HEALTHY-reading articles whose L2 shift is
# recent and decoupled from any structural removal pivot.
FALLBACK_PIVOT = {"Nakba": "2023-10-01", "Zionism": "2023-10-01",
                  "Palestinian political violence": "2023-10-01",
                  "Gaza genocide": "2023-10-01", "Racial conceptions of Jewish identity in Zionism": "2023-10-01",
                  "Genetic studies of Jews": "2023-10-01", "History of Zionism": "2023-10-01"}


# ---- alignment + fetch (spike 012a) ----------------------------------------
def sitelinks(en_title, langs=None):
    """English title -> (Q-id, {lang: title}) via Wikidata sitelinks (authoritative cross-edition map)."""
    r = _S.get(config.WIKIDATA, params={"action": "wbgetentities", "format": "json", "sites": "enwiki",
               "titles": en_title, "props": "sitelinks", "normalize": 1}, timeout=30).json()
    ent = next(iter(r["entities"].values()))
    if "missing" in ent:
        raise LookupError(f"no Wikidata item for enwiki title {en_title!r}")
    links = {}
    for site, info in ent.get("sitelinks", {}).items():
        if site.endswith("wiki") and not site.startswith(
                ("commons", "meta", "species", "wikidata", "sources", "quote", "voyage", "news", "books", "versity")):
            links[site[:-4]] = info["title"]
    if langs:
        links = {l: links[l] for l in langs if l in links}
    return ent["id"], links


def entity_labels(entity_title, langs):
    """English entity title -> {lang: native surface form} (the entity's own article title per edition)."""
    r = _S.get(config.WIKIDATA, params={"action": "wbgetentities", "format": "json", "sites": "enwiki",
               "titles": entity_title, "props": "sitelinks", "normalize": 1}, timeout=30).json()
    ent = next(iter(r["entities"].values()))
    out = {"en": entity_title}
    for lang in langs:
        sl = ent.get("sitelinks", {}).get(f"{lang}wiki")
        if sl:
            out[lang] = sl["title"]
    return out


def fetch_asof(lang, title, ts=None):
    """Wikitext for `title` on {lang}.wikipedia — current, or the last revision <= ts.
    Returns (revid, timestamp, raw_wikitext, prose) or (None, None, "", "")."""
    params = {"action": "query", "format": "json", "formatversion": "2", "prop": "revisions",
              "titles": title, "rvprop": "ids|timestamp|content", "rvslots": "main", "redirects": 1}
    if ts:
        params["rvstart"] = ts
        params["rvlimit"] = 1
    r = _S.get(config.action(lang), params=params, timeout=30).json()
    try:
        rev = r["query"]["pages"][0]["revisions"][0]
        content = rev["slots"]["main"]["content"]
    except (KeyError, IndexError):
        return None, None, "", ""
    return rev["revid"], rev["timestamp"], content, mwparserfromhell.parse(content).strip_code()


def prose_asof(lang, title, ts=None):
    return fetch_asof(lang, title, ts)[3]


def _select_established_langs(links, max_langs=DEFAULT_COMPARE_LANGS, pinned=None):
    """Choose a stable, topic-specific default language set.

    Strategy:
    - pin SLATE langs first (always included if available in links),
    - fill remaining slots with established editions ranked by prose length,
    - total cap = max(max_langs, len(pinned) + 2) so SLATE always gets extras,
    - keep English in the set when available (pivot-relative read is en-anchored).
    """
    if not links:
        return []

    pinned_valid = [l for l in (pinned or []) if l in links]
    cap = max(max_langs, len(pinned_valid) + 2) if pinned_valid else max_langs

    major_pool = [l for l in MAJOR_LANG_PRIORITY if l in links]
    pool = major_pool or sorted(links)
    priority = {lang: idx for idx, lang in enumerate(MAJOR_LANG_PRIORITY)}

    scored = []
    for lang in pool:
        if lang in pinned_valid:
            continue
        try:
            prose = prose_asof(lang, links[lang], None)
            chars = len(prose or "")
        except Exception:  # noqa: BLE001
            chars = 0
        scored.append((lang, chars))

    scored.sort(key=lambda row: (-row[1], priority.get(row[0], 999), row[0]))
    extras_needed = cap - len(pinned_valid)
    extras = [lang for lang, chars in scored if chars > 0][:extras_needed]
    if not extras:
        extras = [lang for lang, _ in scored][:extras_needed]

    chosen = pinned_valid + extras

    if "en" in links and "en" not in chosen:
        if len(chosen) < cap:
            chosen.append("en")
        elif chosen:
            chosen[-1] = "en"

    out = []
    for lang in chosen:
        if lang not in out:
            out.append(lang)
    return out


# ---- stance per edition + divergence (spikes 012b/012c) --------------------
def _sval(rec):
    return STANCE_VAL.get(rec["stance"], 0) if rec else None


def _edition_stances(client, prose, ents, native, max_chars=MAX_CHARS):
    """Return (focal, lead) dicts {entity: record} for one edition's native prose."""
    pa = focal_passage(prose, native, max_chars) or prose[:max_chars]
    pb = prose[:max_chars]
    focal = {r["entity"]: r for r in classify(client, ents, pa)}
    lead = {r["entity"]: r for r in classify(client, ents, pb)}
    return focal, lead


def static_divergence(client, article, langs, prose_by_lang, ents, labels):
    """Per-variant mean cross-edition stance spread (0=agree … 2=max). Returns dict + per-edition stances."""
    editions = {}
    for l in langs:
        native = [labels[e].get(l, e) for e in ents]
        f, ld = _edition_stances(client, prose_by_lang[l], ents, native)
        editions[l] = {"focal": f, "lead": ld}
    out = {"variants": {}, "editions": editions}
    for variant in ("lead", "focal"):
        spreads = {}
        for e in ents:
            vals = [_sval(editions[l][variant].get(e)) for l in langs]
            vals = [v for v in vals if v is not None]
            if len(vals) >= 2:
                spreads[e] = max(vals) - min(vals)
        out["variants"][variant] = {"divergence": round(sum(spreads.values()) / len(spreads), 2) if spreads else 0.0,
                                    "spreads": spreads}
    return out


def _en_gap(vals_by_lang, ents):
    gaps = []
    for e in ents:
        en = vals_by_lang.get("en", {}).get(e)
        others = [vals_by_lang[l].get(e) for l in vals_by_lang if l != "en" and vals_by_lang[l].get(e) is not None]
        if en is not None and others:
            gaps.append(abs(en - sum(others) / len(others)))
    return round(sum(gaps) / len(gaps), 2) if gaps else 0.0


def _l1_pivot(article):
    """Return a supported pivot date, suppressing candidates rejected by fresh L1 confirmation."""
    from . import pipeline

    con = duckdb.connect(str(config.DB), read_only=True)
    try:
        corpus = Corpus(con)
        horizon = corpus.latest_snapshot(article)
        confirmation = drift.load_confirmation(article)
        if pipeline.confirmation_is_fresh(confirmation, horizon):
            if confirmation.get("status") != "confirmed":
                return None, "fresh L1 confirmation rejected the candidate"
            episode = (confirmation.get("confirmed_episodes") or [])[0]
            return episode["candidate_start"], "confirmed L1"
        d = drift.verdict_dict(con, article)
    finally:
        con.close()
    eps = d.get("episodes", [])
    if eps:
        recent = min(eps, key=lambda e: e["age_years"])
        return recent["start"], f"L1 (peak {recent['peak_pct']}%, {recent['pwr_mass']:,} PWR, age {recent['age_years']}yr)"
    return FALLBACK_PIVOT.get(article, "2023-10-01"), "fallback (L1=HEALTHY — addition-side growth)"


def _read_gap(gb, ga, eps=0.25):
    """Classify the English-vs-others gap change across the pivot. A move larger than `eps` in either
    direction is a real read; anything within the band is 'no net change' (guards against LLM jitter)."""
    return "PEELED AWAY" if ga > gb + eps else ("converged" if ga < gb - eps else "no net change")


def pivot_relative(client, article, langs, links, ents, labels):
    """English-vs-others stance gap before vs after the L1 pivot."""
    pivot_date, src = _l1_pivot(article)
    if pivot_date is None:
        return None
    snap = {"before": {}, "after": {}}
    for when, ts in (("before", f"{pivot_date}T00:00:00Z"), ("after", None)):
        for l in langs:
            prose = prose_asof(l, links[l], ts)
            if prose:
                recs = {r["entity"]: r for r in classify(client, ents, prose[:MAX_CHARS])}
                snap[when][l] = {e: _sval(recs.get(e)) for e in ents}
    gb, ga = _en_gap(snap["before"], ents), _en_gap(snap["after"], ents)
    read = _read_gap(gb, ga)
    return {"pivot": pivot_date, "pivot_source": src, "en_gap_before": gb, "en_gap_after": ga,
            "read": read, "before": snap["before"], "after": snap["after"]}


def emit_findings(article, qid, langs, ents, meta, stat, pr=None, model_contract=None):
    """Persist viewer-shaped findings (receipts + stance + divergence) into config.FINDINGS,
    mirroring the frozen 012a/012b/012c shapes so a NEW article flows straight to the site."""
    slug = config.slugify(article)
    config.write_findings(f"{slug}.receipts.json", {"article": article, "qid": qid, "editions": meta})
    config.write_findings(f"{slug}.stance.json",
                          {"schema_version": STANCE_SCHEMA_VERSION,
                           "prompt_version": STANCE_PROMPT_VERSION,
                           "model_contract": model_contract,
                           "article": article, "langs": langs, "entities": ents,
                           "editions": stat["editions"]})
    div = config.load_findings("divergence.json", {"static": {}, "pivot_relative": {}})
    div.setdefault("static", {})[article] = {
        "schema_version": STANCE_SCHEMA_VERSION,
        "prompt_version": STANCE_PROMPT_VERSION,
        "model_contract": model_contract,
        "variants": stat["variants"],
    }
    if pr:
        div.setdefault("pivot_relative", {})[article] = pr
    config.write_findings("divergence.json", div)


def crosslingual(article, langs=None, pivot=True, persist=True, provider=None, model=None, base_url=None,
                 client=None, context=None):
    """Run the cross-language stance comparison for one article; print + return the report.
    Persists viewer-shaped findings unless persist=False (tests). `client` is the injectable LLM port —
    built from provider/model/base_url when None (CLI path), injected by the pipeline (shared client)."""
    requested_langs = list(langs) if langs else None
    ents = list((context or {}).get("entities") or default_entities(article))
    qid, links = sitelinks(article, None)
    if requested_langs:
        langs = [l for l in requested_langs if l in links]
    else:
        langs = _select_established_langs(links, pinned=SLATE.get(article))
    langs = [l for l in langs if l in links]
    labels = {e: entity_labels(e, langs) for e in ents}
    prose_by_lang, meta = {}, {}
    for l in langs:
        revid, ts, _, prose = fetch_asof(l, links[l])
        prose_by_lang[l] = prose
        meta[l] = {"present": True, "revid": revid, "title": links[l], "timestamp": ts, "prose_chars": len(prose)}
    if client is None:
        from . import llm
        client = llm.make_client(provider, model, base_url)

    print(f"=== CROSS-LANGUAGE STANCE COMPARISON — {article}  ({'/'.join(langs)}) ===")
    if not requested_langs:
        print("  default editions: auto-selected established languages for this topic")
    if context:
        print(f"  context: L2/L2.5 feed active (entities={ents})")
    stat = static_divergence(client, article, langs, prose_by_lang, ents, labels)
    print("  STATIC divergence (0=agree … 2=max):")
    for v in ("lead", "focal"):
        d = stat["variants"][v]
        print(f"    [{v:>5}] {d['divergence']:.2f}   {d['spreads']}")
    model_contract = {
        "provider": getattr(client, "provider", None),
        "model": getattr(client, "model", None),
        "prompt_version": STANCE_PROMPT_VERSION,
    }
    result = {"article": article, "langs": langs, "static": stat,
              "model_contract": model_contract}
    pr = None
    if pivot:
        pr = pivot_relative(client, article, langs, links, ents, labels)
        if pr:
            print(f"  PIVOT-RELATIVE: pivot {pr['pivot']} [{pr['pivot_source']}]")
            print(f"    English-vs-others gap: before {pr['en_gap_before']} → after {pr['en_gap_after']}  ⇒ {pr['read']}")
            result["pivot_relative"] = pr
        else:
            print("  PIVOT-RELATIVE: skipped (fresh L1 confirmation rejected the candidate)")
    if persist:
        emit_findings(article, qid, langs, ents, meta, stat, pr, model_contract)
    print("  (LEAD, not a verdict — makes cross-lingual disagreement legible.)")
    return result
