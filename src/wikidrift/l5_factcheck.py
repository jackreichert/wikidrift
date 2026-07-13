"""L5 instrument #2 — cross-edition citation + claim divergence (promoted from spike 014).

Closes the gap instrument #1 (cross-lingual STANCE, `l5_crosslingual`) can't: *factual/numerical*
distortion (KL Warschau's victim-count myth reads flat to stance). Two signals across editions,
as-of aware (the temporal analogue of #1's pivot-relative mode):

  citation — cited-domain overlap (Jaccard). CONFOUNDED by edition language (low even for a neutral
             control that agrees on facts) → treat as CONTEXT, not a flag.
  claim    — per-edition answers to load-bearing factual questions (Claude, native), then a
             cross-edition adjudication: agree / differ / contradict. This is the reliable signal.

A CONTRADICT verdict is a LEAD for a researcher, never a verdict. Needs an LLM key (default Anthropic;
pick a cheaper/local backend via --provider/--model/--base-url or WIKIDRIFT_LLM_* env — see llm.py).
"""
from . import config
from .l5_crosslingual import sitelinks, fetch_asof

MAX_CHARS = 8000

# Load-bearing factual questions per article (transparent — the researcher picks them).
QUESTIONS = {
    "Warsaw concentration camp": [
        "What is the estimated total number of people killed at this camp?",
        "Who were the primary victims (e.g. Jews, ethnic Poles, Soviet POWs)?",
        "Was it primarily an extermination/death camp, or a concentration/labor camp?",
    ],
    "Photosynthesis": [
        "What are the inputs and outputs of the overall photosynthesis reaction?",
        "In which cellular structure does photosynthesis take place?",
    ],
    "Nakba": [
        "Approximately how many Palestinians were displaced?",
        "Is the event described as ethnic cleansing?",
    ],
    # Framing case (complement to Nakba): factual anchors are expected to AGREE across editions;
    # the divergence lives in framing (instrument #1). Validated Session 05 (2026-07-09).
    "Zionism": [
        "In what year and city was the First Zionist Congress held, and who convened it?",
        "In what geographic region did the Zionist movement seek to establish a Jewish homeland?",
        "Approximately when did the modern political Zionist movement emerge?",
    ],
    # --- expansion slate (Session 07; from the run-list). Keep these FACTUAL — framing is instrument #1's job. ---
    "Hamas": [
        "In what year was Hamas founded, and by whom?",
        "Which governments or bodies have officially designated Hamas (or its military wing) a terrorist organization?",
        "What does Hamas's founding charter state regarding the state of Israel?",
    ],
    "Israeli–Palestinian conflict": [
        "When is the conflict said to have begun?",
        "What are the core disputed issues the article lists (borders, Jerusalem, refugees, settlements)?",
    ],
    "Jedwabne pogrom": [
        "How many Jews were killed in the Jedwabne pogrom?",
        "Who carried out the killings — local Polish residents or German forces?",
        "What was the role of the German authorities?",
    ],
    "Naliboki massacre": [
        "How many people were killed in the Naliboki massacre?",
        "Who was responsible — Soviet partisans, the Bielski partisans, or others?",
    ],
    "Rescue of Jews by Poles during the Holocaust": [
        "Approximately how many Jews were saved by Poles during the Holocaust?",
        "What was the penalty in German-occupied Poland for helping Jews?",
    ],
    "Palestinian political violence": [
        "What time period and categories of events does the article cover?",
    ],
    "Gaza war": [
        "How many total deaths are reported, and among whom?",
        "On what date did the war begin?",
    ],
    # --- Session 08 (charged-relevant completion set). Neutral, factual anchors — framing is instrument #1. ---
    "Palestine": [
        "When did the British Mandate for Palestine begin and end?",
        "What is the stated origin of the name 'Palestine'?",
        "What was the approximate population and its religious/ethnic composition in Palestine circa 1900?",
    ],
    "UNRWA": [
        "In what year was UNRWA established, and by which body?",
        "How does UNRWA define a 'Palestine refugee', and does that definition include descendants?",
        "Which populations and territories does UNRWA's mandate cover?",
    ],
    "Anti-Zionism": [
        "When did anti-Zionism emerge as a distinct position?",
        "Which religious and political groups does the article identify as holding anti-Zionist positions?",
    ],
    "Collaboration in German-occupied Poland": [
        "Did the Polish state or its government-in-exile formally collaborate with Nazi Germany?",
        "What scale of individual Polish collaboration with the occupier does the article describe?",
        "What role does the article attribute to Poles in the deaths of Jews under occupation?",
    ],
    "History of Zionism": [
        "In what year and city was the First Zionist Congress held, and who convened it?",
        "Approximately when did the modern political Zionist movement emerge?",
        "In what geographic region did the movement seek to establish a Jewish homeland?",
    ],
    "Genetic studies of Jews": [
        "Do genetic studies indicate that major Jewish populations share common Middle Eastern / Levantine ancestry?",
        "What do studies conclude about the ancestry of Ashkenazi Jews?",
    ],
    "Racial conceptions of Jewish identity in Zionism": [
        "Did early Zionist thinkers employ racial or ethnic conceptions of Jewish identity?",
        "How does the article characterize the relationship between Zionism and contemporaneous race science?",
    ],
    "Bar Kokhba Revolt": [
        "Approximately how many Jewish casualties resulted from the Bar Kokhba revolt?",
        "What were the revolt's consequences for the Jewish population of Judea?",
        "Did the Romans rename the province after the revolt, and to what name?",
    ],
    "Gaza genocide": [
        "Which bodies, states, or scholars have characterized events in Gaza as genocide, and which reject that characterization?",
        "What casualty figures does the article cite?",
        "What is the status of genocide allegations in international legal proceedings (e.g. the ICJ)?",
    ],
}

EXTRACT_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["answers"],
    "properties": {"answers": {"type": "array", "items": {
        "type": "object", "additionalProperties": False,
        "required": ["question", "answer", "value", "evidence"],
        "properties": {"question": {"type": "string"}, "answer": {"type": "string"},
                       "value": {"type": "string"}, "evidence": {"type": "string"}}}}},
}
ADJ_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["questions"],
    "properties": {"questions": {"type": "array", "items": {
        "type": "object", "additionalProperties": False,
        "required": ["question", "verdict", "note"],
        "properties": {"question": {"type": "string"},
                       "verdict": {"type": "string", "enum": ["agree", "differ", "contradict", "insufficient"]},
                       "note": {"type": "string"}}}}},
}
EXTRACT_PROMPT = """Answer each question STRICTLY from this Wikipedia passage (it may be non-English).
For each: a short 'answer', a normalized 'value' (a number/range/short category, or "not stated"),
and a short 'evidence' quote. Questions:
{qs}

PASSAGE:
{passage}"""
ADJ_PROMPT = """Below are answers to the same questions, each from a different-language Wikipedia edition
of the SAME article. For each question judge ACROSS editions:
  - "agree": consistent facts.  - "differ": different detail/emphasis, not incompatible.
  - "contradict": assert INCOMPATIBLE facts (e.g. different numbers/categories).
  - "insufficient": not enough stated.
Treat a large NUMERIC gap as "contradict". Add a one-line 'note'. This is a LEAD, not a verdict.

{payload}"""


def _context_block(context):
    if not context:
        return ""
    lines = []
    if context.get("router_leads"):
        lines.append(f"Router leads: {', '.join(context['router_leads'])}")
    if context.get("entities"):
        lines.append(f"Focal entities: {', '.join(context['entities'])}")
    l2 = context.get("l2_shifts") or {}
    shifted = [e for e, x in l2.items() if x.get("shifted")]
    if shifted:
        lines.append(f"L2 shifted entities: {', '.join(shifted)}")
    lex = context.get("lexical") or {}
    if lex.get("js_divergence") is not None:
        lines.append(f"Lexical JS divergence: {lex['js_divergence']}")
    if not lines:
        return ""
    return "\nContext from earlier layers:\n" + "\n".join(f"- {ln}" for ln in lines) + "\n"


def _domains(raw):
    """Citation domains for the cross-edition Jaccard overlap (shared parser; no Wayback unwrap here —
    overlap is CONTEXT only, so archive wrappers are harmless)."""
    return config.citation_domains(raw)


def _jaccard(a, b):
    a, b = set(a), set(b)
    return len(a & b) / len(a | b) if (a | b) else 1.0


def _call(client, schema, prompt, max_tokens=1600):
    return client.complete_json(schema, prompt, max_tokens)


def factcheck(article, langs=None, ts=None, persist=True, provider=None, model=None, base_url=None,
              client=None, context=None):
    """Cross-edition citation + claim divergence for one article (as-of aware). Print + return.
    Persists a viewer-shaped findings file unless persist=False (tests). `client` is the injectable LLM
    port — built from provider/model/base_url when None (CLI path), injected by the pipeline."""
    _, links = sitelinks(article, langs)
    langs = [l for l in (langs or links) if l in links]
    if client is None:
        from . import llm
        client = llm.make_client(provider, model, base_url)
    tag = f" @ {ts[:10]}" if ts else " (now)"
    print(f"=== L5 fact-check (citation+claim) — {article}{tag}  ({'/'.join(langs)}) ===")

    # citation divergence
    dom = {}
    for l in langs:
        _, _, raw, _ = fetch_asof(l, links[l], ts)
        dom[l] = _domains(raw)
    pairs = [(langs[i], langs[j]) for i in range(len(langs)) for j in range(i + 1, len(langs))]
    js = [_jaccard(dom[a], dom[b]) for a, b in pairs] or [1.0]
    mean_j = round(sum(js) / len(js), 2)
    print(f"  CITATION overlap (Jaccard, CONTEXT only) = {mean_j}  domains/edition: "
          + ", ".join(f"{l}:{len(dom[l])}" for l in langs))

    # claim divergence
    qs = QUESTIONS.get(article, [])
    per = {}
    for l in langs:
        _, _, _, prose = fetch_asof(l, links[l], ts)
        ans = _call(client, EXTRACT_SCHEMA,
                    EXTRACT_PROMPT.format(qs="\n".join(f"- {q}" for q in qs), passage=prose[:MAX_CHARS]))["answers"]
        per[l] = {a["question"]: a for a in ans}
    lines = []
    for q in qs:
        lines.append(f"Q: {q}")
        for l in langs:
            a = per[l].get(q, {})
            lines.append(f"  [{l}] value={a.get('value', '?')!r} — {a.get('answer', '')[:180]}")
    payload = _context_block(context) + "\n".join(lines)
    adj = _call(client, ADJ_SCHEMA, ADJ_PROMPT.format(payload=payload))["questions"]
    print("  CLAIM divergence (the reliable signal):")
    for a in adj:
        mark = "‼" if a["verdict"] == "contradict" else (" " if a["verdict"] == "agree" else "·")
        print(f"    [{a['verdict']:>11}]{mark} {a['question'][:58]}\n        {a['note'][:110]}")
    res = {"article": article, "asof": ts, "langs": langs,
           "citation": {"mean_jaccard": mean_j, "domains_per_edition": {l: len(dom[l]) for l in langs}},
           "claim": {"per_edition": per, "adjudication": adj}}
    if persist:
        slug = config.slugify(article) + (f".asof-{ts[:10]}" if ts else "")
        config.write_findings(f"{slug}.factcheck.json", res)
    return res
