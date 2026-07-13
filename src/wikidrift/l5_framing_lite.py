"""L5 Framing Lite — pivot-gated cross-lingual lead-section divergence.

Asks: when the English article had a pivot event (long-stable content removed), did other language
editions keep the framing English lost? A divergence corroborates the pivot as a contested edit,
not a corrective one.

Lighter than L5 #1 (crosslingual): lead sections only, pivot-gated, Haiku-capable.

Language selection (per article):
  1. Categorize via LLM (cached as {slug}.category.json) → SLATE editions for that category
  2. Add top-2 non-EN editions by byte length (MediaWiki action=query&prop=info)
  3. Deduplicate, cap at 5 total, skip stubs (< 2 kB)

Output: {slug}.framing.json in the findings directory.
"""
from __future__ import annotations

import datetime as dt

from . import config
from .config.parsing import slugify
from .l5_crosslingual import sitelinks

_S = config.session()

MAX_LEAD_CHARS = 3000    # per-edition lead cap (keeps Haiku token budget sane)
MAX_EDITIONS = 5
MIN_EDITION_BYTES = 2000
TOP_N_BY_LENGTH = 2

CATEGORIES = ["israeli-palestinian", "polish-wwii", "general"]

# SLATE: editorially important editions per category (EN always added separately)
CATEGORY_SLATE: dict[str, list[str]] = {
    "israeli-palestinian": ["ar", "he"],
    "polish-wwii": ["pl", "de"],
    "general": [],
}

# --- JSON Schemas ----------------------------------------------------------------

_CATEGORIZE_SCHEMA = {
    "type": "object",
    "properties": {
        "category": {"type": "string", "enum": CATEGORIES},
        "confidence": {"type": "number"},
        "reason": {"type": "string"},
    },
    "required": ["category", "confidence", "reason"],
    "additionalProperties": False,
}

_DIVERGENCE_ITEM = {
    "type": "object",
    "properties": {
        "topic": {"type": "string"},
        "editions_differ": {"type": "array", "items": {"type": "string"}},
        "en_says": {"type": "string"},
        "other_says": {"type": "string"},
        "verdict": {"type": "string", "enum": ["differ", "contradict", "absent_en", "absent_other", "agree"]},
        "evidence_en": {"type": ["string", "null"]},
        "evidence_other": {"type": ["string", "null"]},
    },
    "required": ["topic", "editions_differ", "en_says", "other_says", "verdict", "evidence_en", "evidence_other"],
    "additionalProperties": False,
}

_DIVERGENCE_SCHEMA = {
    "type": "object",
    "properties": {
        "divergences": {"type": "array", "items": _DIVERGENCE_ITEM},
        "summary": {"type": "string"},
    },
    "required": ["divergences", "summary"],
    "additionalProperties": False,
}


# --- helpers ---------------------------------------------------------------------

def _categorize(article: str, client) -> dict:
    """LLM classification of article into a topic category. Cached."""
    slug = slugify(article)
    cached = config.load_findings(f"{slug}.category.json")
    if cached:
        return cached

    prompt = (
        f'Classify the following Wikipedia article into exactly one category.\n'
        f'Categories:\n'
        f'  "israeli-palestinian" — articles about Israel, Palestine, Zionism, Gaza, Hamas, and related topics\n'
        f'  "polish-wwii" — articles about Poland, the Holocaust, WWII Polish history, and related topics\n'
        f'  "general" — everything else\n\n'
        f'Article: "{article}"\n\n'
        f'Return JSON with keys: category, confidence (0.0–1.0), reason (one sentence).'
    )
    result = client.complete_json(_CATEGORIZE_SCHEMA, prompt, max_tokens=256)
    config.write_findings(f"{slug}.category.json", result)
    return result


def _fetch_lead(lang: str, title: str) -> str:
    """Fetch the lead (intro) section of a Wikipedia article as plain text."""
    try:
        r = _S.get(config.action(lang), params={
            "action": "query", "format": "json", "formatversion": "2",
            "prop": "extracts", "exintro": 1, "explaintext": 1,
            "titles": title, "redirects": 1,
        }, timeout=20).json()
        pages = r.get("query", {}).get("pages", [])
        if pages:
            return (pages[0].get("extract") or "").strip()
    except Exception:  # noqa: BLE001
        pass
    return ""


def _edition_lengths(links: dict[str, str]) -> dict[str, int]:
    """Fetch byte lengths for a set of {lang: title} links via action=query&prop=info."""
    lengths: dict[str, int] = {}
    for lang, title in links.items():
        if lang == "en":
            continue
        try:
            r = _S.get(config.action(lang), params={
                "action": "query", "format": "json", "formatversion": "2",
                "prop": "info", "titles": title, "redirects": 1,
            }, timeout=10).json()
            pages = r.get("query", {}).get("pages", [])
            if pages:
                lengths[lang] = pages[0].get("length", 0)
        except Exception:  # noqa: BLE001
            lengths[lang] = 0
    return lengths


def _select_editions(category: str, links: dict[str, str], lengths: dict[str, int]) -> list[str]:
    """Build the final edition list: EN + SLATE + top-N by length, capped at MAX_EDITIONS."""
    slate = [l for l in CATEGORY_SLATE.get(category, []) if l in links]

    already = {"en"} | set(slate)
    by_length = sorted(
        [(lang, lengths.get(lang, 0)) for lang in links if lang not in already],
        key=lambda x: -x[1],
    )
    top = [lang for lang, length in by_length if length >= MIN_EDITION_BYTES][:TOP_N_BY_LENGTH]

    editions = ["en"] + slate + top
    # deduplicate, preserving order
    seen: set[str] = set()
    result = []
    for e in editions:
        if e not in seen and e in links:
            seen.add(e)
            result.append(e)
    return result[:MAX_EDITIONS]


def _compare_leads(article: str, lead_texts: dict[str, str], pivot_window: dict | None, client) -> dict:
    """LLM divergence analysis across lead sections."""
    pivot_note = ""
    if pivot_window:
        pivot_note = (
            f"\nContext: The English edition had a significant content removal event between "
            f"{pivot_window.get('start', '?')} and {pivot_window.get('end', '?')} "
            f"(PWR mass ≈ {pivot_window.get('pwr_mass', '?')}). Focus on whether other editions "
            f"still contain framing that English lost during this window.\n"
        )

    sections = "\n\n".join(
        f"=== {lang.upper()} ===\n{text[:MAX_LEAD_CHARS]}"
        for lang, text in lead_texts.items()
        if text
    )

    prompt = (
        f"You are comparing how different Wikipedia language editions describe the same topic.\n\n"
        f"Article: {article}{pivot_note}\n\n"
        f"Below are the lead (introduction) sections of the article in each edition:\n\n"
        f"{sections}\n\n"
        f"Task: Identify divergences — places where one edition includes significant claims, "
        f"context, or framing that another omits or contradicts. Focus on factual claims, "
        f"causal attributions, named entities/events, and characterizations. Ignore stylistic "
        f"differences and length variation alone.\n\n"
        f"For each genuine divergence:\n"
        f"  topic: short label (e.g. 'civilian casualties', 'cause of conflict')\n"
        f"  editions_differ: the edition codes that diverge on this topic\n"
        f"  en_says: what the EN edition says (or 'not mentioned')\n"
        f"  other_says: what the other edition(s) say (or 'not mentioned')\n"
        f"  verdict: differ | contradict | absent_en | absent_other | agree\n"
        f"  evidence_en: direct quote from EN (or null)\n"
        f"  evidence_other: direct quote from the other edition (or null)\n\n"
        f"Return empty divergences list if editions are substantively aligned. "
        f"A divergence is a lead for a researcher, never a verdict about manipulation."
    )
    return client.complete_json(_DIVERGENCE_SCHEMA, prompt, max_tokens=1500)


# --- main entry point ------------------------------------------------------------

def framing_lite(
    article: str,
    pivot_window: dict | None = None,
    client=None,
    recategorize: bool = False,
    provider: str | None = None,
    model: str | None = None,
    base_url: str | None = None,
) -> dict:
    """Run Framing Lite for one article. Returns and writes the findings dict.

    pivot_window: optional {"start": "YYYY-MM-DD", "end": "YYYY-MM-DD", "pwr_mass": N}
    client: pre-built LLM client (shared with pipeline); built from provider/model/base_url if omitted.
    recategorize: force re-run of the category LLM call (ignore cache).
    """
    if client is None:
        from . import llm as llm_backend
        client = llm_backend.make_client(provider, model, base_url)

    slug = slugify(article)
    print(f"\n=== FRAMING LITE — {article} ===")

    # 1. Categorize
    if recategorize:
        cat_file = config.FINDINGS / f"{slug}.category.json"
        if cat_file.exists():
            cat_file.unlink()
    cat = _categorize(article, client)
    category = cat.get("category", "general")
    print(f"  category: {category} (confidence={cat.get('confidence', '?'):.2f})")

    # 2. Resolve interlanguage links
    try:
        qid, links = sitelinks(article)
    except LookupError as e:
        print(f"  sitelinks: {e}")
        return {"article": article, "error": str(e)}
    print(f"  editions available: {len(links)}")

    # 3. Fetch byte lengths for non-EN editions
    lengths = _edition_lengths(links)

    # 4. Select editions
    editions = _select_editions(category, links, lengths)
    if "en" not in editions:
        print("  warning: English edition not available — skipping")
        return {"article": article, "error": "English edition unavailable"}
    if len(editions) < 2:
        print("  only one edition available — nothing to compare")
        return {"article": article, "editions_compared": editions, "divergences": [], "summary": "Only one edition available."}
    print(f"  comparing: {editions}")

    # 5. Fetch lead sections
    lead_texts: dict[str, str] = {}
    for lang in editions:
        title = links.get(lang, article) if lang != "en" else article
        text = _fetch_lead(lang, title)
        if text:
            lead_texts[lang] = text
            print(f"  {lang}: {len(text)} chars")
        else:
            print(f"  {lang}: empty — skipping")

    if len(lead_texts) < 2:
        print("  fewer than 2 editions have content — skipping comparison")
        return {"article": article, "editions_compared": editions, "divergences": [], "summary": "Insufficient edition content."}

    # 6. LLM divergence comparison
    comparison = _compare_leads(article, lead_texts, pivot_window, client)
    divergences = comparison.get("divergences") or []
    print(f"  divergences: {len(divergences)} ({sum(1 for d in divergences if d.get('verdict') == 'contradict')} contradict)")

    # 7. Write findings
    out = {
        "article": article,
        "run_ts": dt.datetime.utcnow().isoformat() + "Z",
        "category": category,
        "pivot_window": pivot_window,
        "editions_compared": editions,
        "lead_chars": {lang: len(text) for lang, text in lead_texts.items()},
        "divergences": divergences,
        "summary": comparison.get("summary", ""),
        "model": getattr(client, "model", None),
    }
    config.write_findings(f"{slug}.framing.json", out)
    print(f"  wrote findings/{slug}.framing.json")
    return out
