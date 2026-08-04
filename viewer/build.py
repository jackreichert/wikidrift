"""viewer/build.py — static findings-site generator for GitHub Pages.

Reads frozen findings JSON (no tool, no API, no keys) and renders a static site into `docs/`.
Family chrome matches encyclopediae.org (Source Sans, light header, dark footer).

Run:    python viewer/build.py
Deploy: GitHub Pages serves `/docs`; CNAME = wikidrift.encyclopediae.org
"""
import argparse
import difflib
import html
import itertools
import json
import pathlib
import re
import urllib.parse
from collections import Counter
from dataclasses import dataclass, field

import duckdb
import markdown as _md

from wikidrift import pipeline, trust
from wikidrift.corpus import Corpus

ROOT = pathlib.Path(__file__).resolve().parents[1]
FIND = ROOT / ".planning" / "spikes" / "data" / "findings"
ARTICLES = ROOT / ".planning" / "spikes" / "data" / "articles"
DATA = pathlib.Path(__file__).resolve().parent / "data"
SITE = ROOT / "docs"
CUSTOM_DOMAIN = "wikidrift.encyclopediae.org"
SITE_ORIGIN = f"https://{CUSTOM_DOMAIN}"
EXCLUDE_ARTICLES = {"Demo Topic"}  # test fixtures — never ship

VIEWER = pathlib.Path(__file__).resolve().parent

# Concise point-of-use microcopy; the linked glossary remains the full definition.
GLOSSARY_TERMS = {
    "persistence-weighted-loss": (
        "persistence-weighted-loss",
        "The share of wording lost, weighted so text that survived more snapshots counts more.",
    ),
    "pwr-mass": (
        "persistence-weighted-loss",
        "The absolute persistence-weighted amount of text lost.",
    ),
    "durable-spine": (
        "durable-spine",
        "The more persistent half of the wording present at the start of a candidate window.",
    ),
    "durable-spine-drop": (
        "durable-spine",
        "The percentage-point decline in durable-spine survival across the whole candidate window.",
    ),
    "rewrite-episode": (
        "rewrite-episode",
        "A bounded window in which established wording was substantially replaced.",
    ),
    "peak-interval": (
        "rewrite-episode",
        "The snapshot interval with the largest persistence-weighted loss in a wider episode.",
    ),
    "coarse-scan": (
        "coarse-exact",
        "A snapshot comparison that finds candidate windows for closer inspection.",
    ),
    "exact-check": (
        "coarse-exact",
        "A revision-level test of whether a candidate window's durable spine collapsed.",
    ),
    "redline": (
        "redline-receipt",
        "A before-and-after view showing wording removed and added.",
    ),
    "receipt": (
        "redline-receipt",
        "A structured record of the evidence inspected, measurements made, and decision reached.",
    ),
    "snapshot": (
        "snapshot-mature-token",
        "An article version selected near a sampling date for interval comparison.",
    ),
    "mature-interval": (
        "snapshot-mature-token",
        "An interval measured after the article has enough tracked text for persistence analysis.",
    ),
    "token": (
        "snapshot-mature-token",
        "One tracked unit of text, usually a word or punctuation mark.",
    ),
}
_GLOSSARY_DESCRIPTION_IDS = itertools.count(1)


def _asset(rel):
    """Read a static template/asset (HTML/CSS/JS) that lives beside build.py, verbatim."""
    return (VIEWER / rel).read_text(encoding="utf-8")


def _md_asset(stem):
    """Compile a Markdown template to HTML. Raw HTML blocks pass through unchanged."""
    text = (VIEWER / f"templates/{stem}.md").read_text(encoding="utf-8")
    return _md.markdown(text, extensions=["extra"])


def _glossary_term(term, label=None):
    """Link a public metric label to its glossary entry with an accessible tooltip."""
    anchor, definition = GLOSSARY_TERMS[term]
    visible_label = label or term.replace("-", " ")
    description_id = f"glossary-description-{next(_GLOSSARY_DESCRIPTION_IDS)}"
    return (
        f'<a class="glossary-term" href="../glossary.html#{anchor}" '
        f'data-tooltip="{esc(definition)}" aria-describedby="{description_id}">'
        f'{esc(visible_label)}</a>'
        f'<span class="sr-only" id="{description_id}">{esc(definition)}</span>'
    )

# Editor tints for the (opt-in) blame overlay — light backgrounds, dark text (AA-safe).
BLAME_PALETTE = ["#f6dede", "#dde6f4", "#dfeede", "#f4eccf", "#e7ddf2", "#d5ecec",
                 "#f4e2cf", "#e4e4e6", "#efdde8", "#dcecdf"]
VCLASS = {"contradict": "c", "differ": "d", "agree": "a", "insufficient": "i"}

# Short section intros (article pages). No glossary required — explain in place.
WHAT = {
    "diff": 'The biggest overhaul windows in the English article. Open one to read what was removed '
            'and what replaced it. A large rewrite means the text changed a lot — not that someone '
            'did something wrong.',
    "blame": 'Who introduced each part of the current opening paragraph. Each color is one Wikipedia account.',
    "sources": 'How the article\'s own footnotes changed across the rewrite: which websites and books '
               'were cited more or less. We only show the mix — we do <b>not</b> rate sources as good or bad.',
    "lexical": 'Words that became more common or less common between the compared versions. Handy for '
               'noticing a shift in topic or tone — not a score of bias.',
    "framing": 'How the opening of the article presents the topic in different languages. '
               'Disagreement is a reason to read carefully, not a final answer.',
    "facts": 'Simple factual questions checked in several languages. Editions may agree, differ, '
             'contradict, or not say enough to tell. That is not a claim about who is right.',
    "stance": 'How each language\'s opening treats the subject: more critical, neutral, or sympathetic. '
              'Click a cell to see the short quote that drove the label.',
}
# Topic grouping for the index filter.
CATEGORY = {
    # Israel–Palestine / Jewish-history thesis cluster
    "Zionism": "Israel–Palestine", "Nakba": "Israel–Palestine", "Hamas": "Israel–Palestine",
    "Israeli–Palestinian conflict": "Israel–Palestine", "Palestinian political violence": "Israel–Palestine",
    "Gaza war": "Israel–Palestine", "Gaza genocide": "Israel–Palestine", "Palestine": "Israel–Palestine",
    "UNRWA": "Israel–Palestine", "Anti-Zionism": "Israel–Palestine", "History of Zionism": "Israel–Palestine",
    "Racial conceptions of Jewish identity in Zionism": "Israel–Palestine",
    "Genetic studies of Jews": "Israel–Palestine", "Bar Kokhba Revolt": "Israel–Palestine",
    "Rafah offensive": "Israel–Palestine",
    # Holocaust in Poland
    "Warsaw concentration camp": "Holocaust in Poland", "Jedwabne pogrom": "Holocaust in Poland",
    "Naliboki massacre": "Holocaust in Poland",
    "Rescue of Jews by Poles during the Holocaust": "Holocaust in Poland",
    "Collaboration in German-occupied Poland": "Holocaust in Poland",
    # Political figures, parties, and ideology
    "Xi Jinping": "Politics & ideology", "Ilhan Omar": "Politics & ideology",
    "Elizabeth Warren": "Politics & ideology",
    "Democratic Party (United States)": "Politics & ideology",
    "Republican Party (United States)": "Politics & ideology",
    "Democratic Socialists of America": "Politics & ideology",
    "Socialism": "Politics & ideology", "Capitalism": "Politics & ideology",
    # Controls / cross-domain (mostly off-site, kept for when they gain findings)
    "Photosynthesis": "Science (control)", "Water": "Science (control)", "Chess": "Science (control)",
    "Brontosaurus": "Science (control)", "Abortion": "Cross-domain", "Climate change": "Cross-domain",
}
DEFAULT_CATEGORY = "Other"
CATEGORY_CACHE = FIND / "topic_categories.json"
CATEGORY_OPTIONS = [
    "Israel–Palestine",
    "Holocaust in Poland",
    "Politics & ideology",
    "Science (control)",
    "Cross-domain",
    "Other",
]


def _category_for(article, categories):
    return categories.get(article, DEFAULT_CATEGORY)


def _load_category_cache(path):
    data = load(path) if path else None
    if not isinstance(data, dict):
        return {}
    cats = data.get("categories")
    if not isinstance(cats, dict):
        return {}
    out = {}
    for k, v in cats.items():
        if isinstance(k, str) and isinstance(v, str) and v in CATEGORY_OPTIONS:
            out[k] = v
    return out


def _save_category_cache(path, categories):
    payload = {"version": 1, "categories": dict(sorted(categories.items()))}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _llm_category_client(provider=None, model=None, base_url=None):
    from wikidrift import llm as llm_backend
    return llm_backend.make_client(provider=provider, model=model, base_url=base_url)


def _llm_categorize_topic(client, article):
    schema = {
        "type": "object",
        "additionalProperties": False,
        "required": ["category"],
        "properties": {
            "category": {"type": "string", "enum": CATEGORY_OPTIONS}
        },
    }
    prompt = (
        "Classify this Wikipedia topic title into one category for website filtering. "
        "Return JSON only.\n"
        f"Topic: {article}\n"
        f"Allowed categories: {', '.join(CATEGORY_OPTIONS)}\n"
        "Use 'Other' when uncertain."
    )
    return client.complete_json(schema, prompt, max_tokens=128)["category"]


def resolve_categories(articles, use_llm=False, refresh=False, cache_path=CATEGORY_CACHE,
                       provider=None, model=None, base_url=None):
    categories = {a: CATEGORY.get(a, DEFAULT_CATEGORY) for a in articles}
    if not use_llm:
        return categories

    cache = _load_category_cache(cache_path)
    uncategorized = [article for article in articles if article not in CATEGORY]
    categories.update({article: cache[article] for article in uncategorized if article in cache})
    needed = [article for article in uncategorized if refresh or article not in cache]
    if not needed:
        return categories

    client = _llm_category_client(provider=provider, model=model, base_url=base_url)
    for article in needed:
        try:
            cat = _llm_categorize_topic(client, article)
        except Exception as e:  # noqa: BLE001
            print(f"category fallback [{article}]: {e}")
            cat = CATEGORY.get(article, DEFAULT_CATEGORY)
        categories[article] = cat
        cache[article] = cat
    _save_category_cache(cache_path, cache)
    return categories


def load(path):
    try:
        return json.loads(pathlib.Path(path).read_text(encoding="utf-8"))
    except Exception:
        return None


def esc(s):
    return html.escape(str(s))


def slugify(a):
    # Collapse path separators (subpage titles, or a crafted 'article' field in a findings file) so a page
    # can't be written outside docs/article/ (CWE-22). Mirrors wikidrift.config.slugify.
    return a.replace(" ", "_").replace("/", "_").replace("\\", "_")


# ---- load all findings ------------------------------------------------------
@dataclass
class Findings:
    """All findings the site renders, keyed by article. One object instead of a positional 10-tuple, so
    adding a findings kind is one field here + one accessor in article_page — not an edit to a tuple and
    an 11-arg signature at three call sites (where a swapped pair would silently render the wrong tab)."""
    receipts: dict = field(default_factory=dict)
    stances: dict = field(default_factory=dict)
    factchecks: dict = field(default_factory=lambda: {})
    diver: dict = field(default_factory=lambda: {"static": {}, "pivot_relative": {}})
    mscore: dict = field(default_factory=dict)
    diffs: dict = field(default_factory=dict)
    blames: dict = field(default_factory=dict)
    pivots: dict = field(default_factory=dict)
    sources: dict = field(default_factory=dict)
    lexical: dict = field(default_factory=dict)
    profiles: dict = field(default_factory=dict)
    framings: dict = field(default_factory=dict)
    confirmations: dict = field(default_factory=dict)
    rewrite_status: dict = field(default_factory=dict)
    trust_report: dict = field(default_factory=lambda: {"published": [], "withheld": []})

    l4: dict = field(default_factory=dict)

    def articles(self):
        """Every public article with a renderable finding (excludes test fixtures)."""
        analyzed = {
            article for article, confirmation in self.confirmations.items()
            if confirmation.get("status") in {"confirmed", "not_confirmed"}
        }
        names = (set(self.pivots) | set(self.diffs) | set(self.lexical) | set(self.sources)
                 | set(self.profiles) | analyzed)
        return sorted(a for a in names if a not in EXCLUDE_ARTICLES)


def _finding_dirs():
    """Return legacy findings first so current article shards take precedence."""
    directories = [FIND]
    if ARTICLES.exists():
        directories.extend(
            article_dir / "findings"
            for article_dir in sorted(ARTICLES.iterdir())
            if article_dir.is_dir() and article_dir.name != "_shared"
        )
    return [directory for directory in directories if directory.exists()]


def _artifact_trust(directory, article, finding, artifact_kind):
    database = directory.parent / "provenance.duckdb"
    if not database.is_file():
        return trust.resolve_artifact_trust(None, article, finding, artifact_kind)
    try:
        con = duckdb.connect(str(database), read_only=True)
        try:
            return trust.resolve_artifact_trust(con, article, finding, artifact_kind)
        finally:
            con.close()
    except (duckdb.Error, OSError) as exc:
        return {
            "status": "withheld",
            "reason": f"trust evidence is unavailable: {type(exc).__name__}",
            "endpoint_policy_version": None,
        }


def _record_trust(report, finding_path, article, artifact_kind, decision):
    bucket = "published" if decision["status"] == "published" else "withheld"
    report[bucket].append({
        "article": article,
        "artifact_kind": artifact_kind,
        "path": finding_path.name,
        **decision,
    })


def _load_article_findings(directories, suffix, trust_report=None):
    findings = {}
    for directory in directories:
        for finding_path in directory.glob(f"*.{suffix}.json"):
            finding = load(finding_path)
            article = finding.get("article") if isinstance(finding, dict) else None
            if article and article not in EXCLUDE_ARTICLES:
                if trust_report is not None:
                    decision = _artifact_trust(directory, article, finding, suffix)
                    _record_trust(trust_report, finding_path, article, suffix, decision)
                    if decision["status"] != "published":
                        continue
                findings[article] = finding
    return findings


def _load_confirmations(directories, trust_report):
    """Load confirmations, checking freshness when the corresponding local corpus is available."""
    confirmations = {}
    for directory in directories:
        for finding_path in directory.glob("*.l1-confirmation.json"):
            confirmation = load(finding_path)
            article = confirmation.get("article") if isinstance(confirmation, dict) else None
            if not article or article in EXCLUDE_ARTICLES:
                continue
            decision = _artifact_trust(directory, article, confirmation, "l1-confirmation")
            _record_trust(trust_report, finding_path, article, "l1-confirmation", decision)
            if decision["status"] != "published":
                reason = (
                    "stale exact confirmation"
                    if decision["status"] == "stale"
                    else f"artifact withheld: {decision['reason']}"
                )
                confirmations[article] = {
                    **confirmation,
                    "status": "unavailable",
                    "reason": reason,
                    "trust_status": decision["status"],
                    "confirmed_episodes": [],
                }
                continue
            database = directory.parent / "provenance.duckdb"
            if database.is_file():
                try:
                    con = duckdb.connect(str(database), read_only=True)
                    try:
                        horizon = Corpus(con).latest_snapshot(article)
                    finally:
                        con.close()
                    is_fresh = pipeline.confirmation_is_fresh(confirmation, horizon)
                except (duckdb.Error, OSError):
                    is_fresh = False
                if not is_fresh:
                    confirmation = {
                        **confirmation,
                        "status": "unavailable",
                        "reason": "stale exact confirmation",
                        "confirmed_episodes": [],
                    }
            confirmations[article] = confirmation
    return confirmations


def gather():
    finding_dirs = _finding_dirs()
    trust_report = {"published": [], "withheld": []}
    receipts = _load_article_findings(finding_dirs, "receipts")
    stances = _load_article_findings(finding_dirs, "stance", trust_report)
    sources = _load_article_findings(finding_dirs, "sources")
    lexical = _load_article_findings(finding_dirs, "lexical", trust_report)
    profiles = _load_article_findings(finding_dirs, "profile")
    framings = _load_article_findings(finding_dirs, "framing")
    confirmations = _load_confirmations(finding_dirs, trust_report)
    factchecks = {}
    for directory in finding_dirs:
        for finding_path in directory.glob("*.factcheck.json"):
            finding = load(finding_path)
            article = finding.get("article") if isinstance(finding, dict) else None
            if article and article not in EXCLUDE_ARTICLES:
                label = "now" if not finding.get("asof") else finding["asof"][:10]
                factchecks.setdefault(article, {})[label] = finding
    diver = {"static": {}, "pivot_relative": {}}
    mscore, l4map = {}, {}
    if FIND.exists():
        fdiv = load(FIND / "divergence.json") or {}
        for k, v in (fdiv.get("static") or {}).items():
            if k not in EXCLUDE_ARTICLES:
                diver["static"][k] = v
        for k, v in (fdiv.get("pivot_relative") or {}).items():
            if k not in EXCLUDE_ARTICLES:
                diver["pivot_relative"][k] = v
        raw_m = load(FIND / "mscore.json") or {}
        mscore.update({k: v for k, v in raw_m.items() if k not in EXCLUDE_ARTICLES})
        l4raw = load(FIND / "l4_discovery.json") or {}
        seed = l4raw.get("seed")

        def _l4_title(item):
            if isinstance(item, str):
                return item
            if isinstance(item, dict):
                return item.get("article") or item.get("title") or item.get("candidate")
            return None

        for x in l4raw.get("retrofit_leads") or []:
            t = _l4_title(x)
            if t and t not in EXCLUDE_ARTICLES:
                l4map[t] = {"seed": seed, "class": "retrofit lead"}
        for x in l4raw.get("born_in_contested") or []:
            t = _l4_title(x)
            if t and t not in EXCLUDE_ARTICLES:
                l4map.setdefault(t, {"seed": seed, "class": "born-in-contested"})
        for row in l4raw.get("retest") or []:
            if not isinstance(row, dict):
                continue
            t = _l4_title(row)
            if t and t not in EXCLUDE_ARTICLES:
                l4map.setdefault(t, {"seed": seed, "class": row.get("class") or row.get("label") or "L4 candidate"})
    diffs, blames, pivots = {}, {}, {}
    rewrite_status = load(DATA / "rewrite_status.json") or {}
    if DATA.exists():
        for f in DATA.glob("*.diff.json"):
            d = load(f)
            if d:
                diffs[d["article"]] = d
        for f in DATA.glob("*.blame.json"):
            d = load(f)
            if d:
                blames[d["article"]] = d
        for f in DATA.glob("*.pivots.json"):
            d = load(f)
            if d:
                pivots[d["article"]] = d
    return Findings(receipts=receipts, stances=stances, factchecks=factchecks, diver=diver, mscore=mscore,
                    diffs=diffs, blames=blames, pivots=pivots, sources=sources, lexical=lexical,
                    profiles=profiles, framings=framings, confirmations=confirmations,
                    rewrite_status=rewrite_status, trust_report=trust_report, l4=l4map)


# ---- shared fragments -------------------------------------------------------
def oldid(lang, revid):
    # esc() both fields: lang/revid are structural (a Wikidata site code + an int) in normal runs, but a
    # findings file is the same untrusted-input boundary as the content fields, so don't skip escaping here.
    return f"https://{esc(lang)}.wikipedia.org/w/index.php?oldid={esc(revid)}"


def _version_records(rec, framing):
    """Return versions aligned with the newest completed cross-language comparison.

    Temporal framing supplies exact before/after snapshots. Static framing has no revision receipts,
    so its language list filters the older receipt artifact when one exists.
    """
    rec = rec or {}
    framing = framing or {}
    editions = framing.get("editions_compared") or []
    snapshots = framing.get("snapshots") or {}
    if _framing_result_available(framing) and snapshots and editions:
        records = []
        for lang in editions:
            for phase in ("before", "after"):
                version = (snapshots.get(phase) or {}).get(lang)
                if version and version.get("revid"):
                    records.append((lang, phase, version))
        if records:
            return records

    current_langs = set(editions) if _framing_result_available(framing) and editions else None
    return [
        (lang, None, version)
        for lang, version in rec.get("editions", {}).items()
        if version.get("present") and version.get("revid") and (current_langs is None or lang in current_langs)
    ]


def receipts_section(rec, framing=None):
    rec = rec or {}
    rows = []
    for lang, phase, e in _version_records(rec, framing):
        link = (
            f'<a href="{oldid(lang, e["revid"])}" target="_blank" rel="noopener">'
            f'open version {esc(e["revid"])}</a>'
        )
        text_length = e.get("prose_chars")
        if text_length is None:
            text_length = len(e.get("lead") or "")
        rows.append(
            f"<tr><td><b>{esc(lang)}</b></td><td>{esc(phase or 'saved')}</td>"
            f"<td>{esc(e.get('title', ''))}</td>"
            f"<td>{link}</td><td>{esc(e.get('timestamp', ''))}</td>"
            f"<td>{text_length:,}</td></tr>"
        )
    qid = rec.get("qid")
    wikidata = (
        ' Wikidata item: <a href="https://www.wikidata.org/wiki/' + esc(qid) + '" target="_blank" '
        f'rel="noopener">{esc(qid)}</a>.'
        if qid else ""
    )
    return (
        '<h2>Versions we used</h2>'
        '<p class="lead">These are the exact public Wikipedia versions behind the checks on this page. '
        f'Open any link to read the original.{wikidata}</p>'
        '<div class="tablewrap"><table><thead><tr>'
        '<th scope="col">language</th><th scope="col">comparison point</th>'
        '<th scope="col">article title</th>'
        '<th scope="col">version</th><th scope="col">when</th>'
        '<th scope="col">text used (chars)</th></tr></thead>'
        f'<tbody>{"".join(rows)}</tbody></table></div>'
    )


def fact_section(article, fcs):
    if not fcs:
        return ""
    times = sorted(fcs, key=lambda t: (t == "now", t))  # dated first, 'now' last
    first = fcs[times[0]]
    adj0 = (first.get("claim") or {}).get("adjudication") or []
    if not adj0:
        return ""
    order = [q["question"] for q in adj0]
    verdict_by = {
        t: {q["question"]: q for q in ((fcs[t].get("claim") or {}).get("adjudication") or [])}
        for t in times
    }
    sev = {"contradict": 3, "differ": 2, "insufficient": 1, "agree": 0}
    # Plain summary counts (latest snapshot)
    latest = times[-1]
    counts = Counter((verdict_by[latest].get(q) or {}).get("verdict", "insufficient") for q in order)
    v_plain = {
        "agree": "agree",
        "differ": "differ",
        "contradict": "contradict",
        "insufficient": "not enough said",
    }
    summary = (
        f'<p class="brief-sum">On the latest check, <b>{len(order)}</b> questions produced: '
        f'{counts.get("contradict", 0)} contradict · {counts.get("differ", 0)} compatible difference · '
        f'{counts.get("agree", 0)} agree · {counts.get("insufficient", 0)} not enough said.</p>'
    )
    thead = "".join(
        f'<th scope="col">{esc("today" if t == "now" else t)}</th>' for t in times
    )
    rows = []
    for q in order:
        cells = []
        for t in times:
            a = verdict_by[t].get(q)
            if a:
                v = a["verdict"]
                label = v_plain.get(v, v)
                if v == "agree":
                    cells.append(f'<td class="muted" style="font-size:.82rem">{esc(label)}</td>')
                else:
                    cells.append(
                        f'<td><span class="badge v-{VCLASS.get(v, "i")}">{esc(label)}</span></td>'
                    )
            else:
                cells.append("<td>—</td>")
        worst = max(
            (verdict_by[t][q] for t in times if q in verdict_by[t]),
            key=lambda a: sev.get(a["verdict"], 0),
            default=None,
        )
        note = (worst or {}).get("note", "")
        rows.append(
            f'<tr><th scope="row">{esc(q)}</th>{"".join(cells)}</tr>'
            f'<tr class="noterow"><td colspan="{len(times) + 1}" class="muted">{esc(note)}</td></tr>'
        )
    return (
        f'<h2>Do the languages agree on basic facts?</h2>'
        f'<p class="lead">{WHAT["facts"]}</p>{summary}'
        f'<div class="tablewrap"><table class="grid"><thead><tr><th scope="col">question</th>{thead}</tr>'
        f'</thead><tbody>{"".join(rows)}</tbody></table></div>'
        '<p class="muted">These checks are starting points for a reader. They do not decide who is right.</p>'
    )


def mscore_section(article, mscore):
    m = mscore.get(article)
    if not m:
        return ""
    rpr = m.get("refined_per_rev", 0)
    contested = rpr >= 5
    cls = "warn" if contested else "ok"
    if contested:
        read = (
            "Editors spent a lot of energy undoing each other’s work (lots of reverts). "
            "A loud fight does not prove anyone is biased."
        )
        label = "lots of edit fighting"
    else:
        read = (
            "The page was not dominated by back-and-forth undoing. "
            "A quiet rewrite can still matter — it just means few people fought it on the page."
        )
        label = "little edit fighting"
    try:
        revs = f'{m["raw"]["revs"]:,}'
    except (KeyError, TypeError):
        revs = "—"
    return (
        f'<div class="signal-card {"hot" if contested else "cool"}">'
        f'<div class="signal-label">How much did editors fight over it?</div>'
        f'<div class="signal-value"><span class="pill {cls}">{esc(label)}</span></div>'
        f'<p class="signal-note">{esc(read)} '
        f'<span class="muted">About {revs} recorded revisions in the measure.</span></p></div>'
    )


# ---- diff (compact, authored) ----------------------------------------------
def _sentences(text):
    return [s for s in re.split(r"(?<=[.!?])\s+", text.strip()) if s]


def _author_of(text, wmap):
    if not wmap:
        return None
    eds = [wmap[w] for w in re.findall(r"[a-z]{3,}", text.lower()) if w in wmap]
    return Counter(eds).most_common(1)[0][0] if eds else None


def redline(before_text, after_text, wa=None, wb=None):
    """In-context 'tracked changes' over stripped PROSE — reads like the article. Deletions struck,
    insertions highlighted and colored by the editor who added them (best-match, not per-token)."""
    b, a = _sentences(before_text), _sentences(after_text)
    sm = difflib.SequenceMatcher(None, b, a, autojunk=False)
    colors, parts = {}, []

    def col(who):
        if not who or who == "?":
            return None
        colors.setdefault(who, BLAME_PALETTE[len(colors) % len(BLAME_PALETTE)])
        return colors[who]

    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            parts.append(f'<span class="eq">{esc(" ".join(b[i1:i2]))} </span>')
            continue
        if i2 > i1:
            who = _author_of(" ".join(b[i1:i2]), wb)
            tt = f' title="was written by {esc(who)}"' if who else ""
            parts.append(f'<del{tt}>{esc(" ".join(b[i1:i2]))}</del> ')
        if j2 > j1:
            who = _author_of(" ".join(a[j1:j2]), wa)
            c = col(who)
            st = f' style="--au:{c}"' if c else ""
            tt = f' title="added by {esc(who)}"' if who else ""
            parts.append(f'<ins{st}{tt}>{esc(" ".join(a[j1:j2]))}</ins> ')
    legend = ""
    if colors:
        chips = " ".join(
            f'<span class="chip" style="background:{c}">{esc(n)}</span>'
            for n, c in list(colors.items())[:12]
        )
        legend = (
            f'<p class="legend">Highlight colors show which account likely added that new text: {chips}</p>'
        )
    return f'<div class="redline">{"".join(parts)}</div>{legend}'


def _text_chunks(before, after, cap=800):
    b, a = _sentences(before), _sentences(after)
    sm = difflib.SequenceMatcher(None, b, a, autojunk=False)
    out, n = [], 0
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            continue
        if n >= cap:
            out.append({"t": "trunc"})
            break
        out.append({"t": tag, "rem": " ".join(b[i1:i2]), "add": " ".join(a[j1:j2])})
        n += 1
    return out


def diff_rows(chunks, lbl_b="before", lbl_a="after"):
    rows = [f'<div class="drow head"><div class="dl">{esc(lbl_b)}</div><div class="dr">{esc(lbl_a)}</div></div>']
    for c in chunks:
        t = c.get("t")
        if t == "equal":
            continue
        if t == "trunc":
            rows.append(
                '<div class="drow"><div class="dfull muted">This was a near-total rewrite — most of the '
                'article changed, so only the first several hundred changes are shown here. Open the full '
                'Wikipedia versions from the <b>Versions</b> tab to read everything.</div></div>'
            )
            continue
        rem, add = c.get("rem", ""), c.get("add", "")
        left = f'<div class="dl del"><span class="mk">−</span>{esc(rem)}</div>' if rem else '<div class="dl empty"></div>'
        right = f'<div class="dr add"><span class="mk">+</span>{esc(add)}</div>' if add else '<div class="dr empty"></div>'
        rows.append(f'<div class="drow">{left}{right}</div>')
    if len(rows) == 1:
        rows.append('<div class="drow"><div class="dfull muted">No prose-level changes at this point.</div></div>')
    return f'<div class="authdiff" role="table" aria-label="before and after, side by side">{"".join(rows)}</div>'


def diff_section(diff):
    return (
        f'<h2>What changed</h2><p class="lead">{WHAT["diff"]}</p>'
        f'<p class="muted"><del>Struck-out text</del> was removed · <ins>highlighted text</ins> was added · '
        f'{esc(diff["before"]["date"])} compared with today.</p>'
        + redline(diff["before"]["text"], diff["after"]["text"])
    )


def _pivot_links(pv, slug):
    pivs = pv.get("pivots") or []
    items = []
    for i, p in enumerate(pivs):
        pct = p.get("peak_pct")
        if pct is not None:
            pct_s = _pwr_read(pct)
        else:
            pct_s = "high persistence-weighted loss"
        items.append(
            f'<li><a class="pv-link" href="{slug}.p{i}.html">'
            f'<span><b>{esc(p["start"])} → {esc(p["end"])}</b>'
            f'<span class="muted"> · {esc(pct_s)}</span></span>'
            f'<span class="f-go" aria-hidden="true">→</span></a></li>'
        )
    return "".join(items)


def render_pivots(pv, slug):
    pivs = pv.get("pivots") or []
    n = len(pivs)
    return (
        f'<h2>Which candidate rewrite windows stood out?</h2>'
        f'<p class="lead">{WHAT["diff"]}</p>'
        f'<p class="brief-sum">The coarse PWR scan found <b>{n}</b> candidate window{"s" if n != 1 else ""}. '
        f'Open one to read the old wording next to the new wording.</p>'
        f'<ul class="pvlinks">{_pivot_links(pv, slug)}</ul>'
    )


def pivot_page(article, p, i):
    slug = slugify(article)
    status = _pivot_status(p)
    title = {
        "confirmed": "Confirmed candidate redline",
        "rejected": "Rejected candidate redline",
    }.get(status, "Candidate redline")
    exact_status = {
        "confirmed": "Exact checking confirmed a rewrite within this broad window",
        "rejected": "Exact checking rejected this candidate",
    }.get(status, "Exact decision unavailable")
    body = (
        f'<div class="page-intro"><p class="kicker"><a href="{slug}.html">← {esc(article)}</a></p>'
        f'<h1>{title} · {esc(p["start"])} → {esc(p["end"])}</h1>'
        f'<p class="summary">The {_glossary_term("peak-interval")} measured '
        f'{_pwr_read(p.get("peak_pct"))}. '
        f'Read it like tracked changes: <del>struck-out text</del> was removed; '
        f'<ins>highlighted text</ins> was added (color hints which account added it).</p>'
        f'<p class="disclaimer">{exact_status}. This is the coarse candidate window, not the exact event pair. '
        f'Something to inspect — not a finished judgment.</p></div>'
        f'<div class="workspace">'
        + redline(p["before_text"], p["after_text"], p.get("authors_after"), p.get("authors_before"))
        + '</div>'
    )
    return render_page(
        title=f"{article} rewrite {p['start']} — WikiDrift",
        body=body, root="../", path=f"article/{slug}.p{i}.html",
        description=f"Before and after text for {article} ({p['start']} → {p['end']}).",
        active="findings",
    )


def blame_section(blame):
    editors = list(dict.fromkeys(s["editor"] for s in blame["spans"]))
    colors = {e: BLAME_PALETTE[i % len(BLAME_PALETTE)] for i, e in enumerate(editors)}
    spans = "".join(
        f'<span class="bl" style="background:{colors[s["editor"]]}" '
        f'title="{esc(s["editor"])} · {esc(s["o_time"])}">{esc(s["text"])} </span>' for s in blame["spans"])
    cnt = Counter(s["editor"] for s in blame["spans"])
    legend = " ".join(f'<span class="chip" style="background:{colors[e]}">{esc(e)} ({n})</span>'
                      for e, n in cnt.most_common(8))
    return (f'<h2>Who wrote the opening</h2><p class="lead">{WHAT["blame"]}</p>'
            f'<p class="muted">Colored by the editor who introduced each part (current revision; hover for '
            f'editor + date). Opening section only.</p><div class="blame">{spans}</div><p class="legend">{legend}</p>')


# ---- headline (plain language) ---------------------------------------------
def _fact_counts(fcs):
    """Return the latest fact-check verdict counts without collapsing evidence states."""
    if not fcs:
        return Counter()
    times = sorted(fcs, key=lambda t: (t == "now", t))
    latest = fcs[times[-1]]
    adj = (latest.get("claim") or {}).get("adjudication") or []
    return Counter(q.get("verdict", "insufficient") for q in adj)


def _pivot_status(pivot):
    """Legacy exports came from verdict_dict and are therefore coarse candidates."""
    return pivot.get("status") or "candidate"


def _pwr_read(pct):
    return f"{pct:.0f}% persistence-weighted loss" if pct is not None else "high persistence-weighted loss"


def _fact_read(counts):
    labels = (
        ("contradict", "contradiction", "contradictions"),
        ("differ", "compatible difference", "compatible differences"),
        ("agree", "agree", "agree"),
        ("insufficient", "not enough", "not enough"),
    )
    parts = []
    for key, singular, plural in labels:
        count = counts.get(key, 0)
        if count:
            parts.append(f"{count} {singular if count == 1 else plural}")
    return " · ".join(parts)


def _top_pivot(article, f):
    pivs = (f.pivots.get(article) or {}).get("pivots") or []
    if not pivs:
        return None
    return max(pivs, key=lambda p: int(p.get("pwr_mass") or 0))


def _rewrite_info(article, f):
    """Return rewrite state and reason without inferring a negative result from missing files."""
    confirmation = f.confirmations.get(article) or {}
    status = confirmation.get("status")
    if status == "confirmed":
        return "finding", None
    if status == "not_confirmed":
        return "none", None
    if status == "unavailable":
        source_state = confirmation.get("source_state") or {}
        if source_state.get("source_status") == "partial":
            reason = source_state.get("reason") or confirmation.get("reason")
        elif confirmation.get("coarse_verdict") == "SKIP":
            reason = "too few snapshots"
        else:
            reason = confirmation.get("reason")
        return "unavailable", reason
    if article in f.pivots or article in f.diffs:
        return "finding", None
    recorded = f.rewrite_status.get(article)
    if isinstance(recorded, dict) and recorded.get("state") in {"none", "unavailable"}:
        return recorded["state"], recorded.get("reason")
    if recorded in {"none", "unavailable"}:
        return recorded, None
    lexical = f.lexical.get(article) or {}
    span = str(lexical.get("span") or "").lower()
    if lexical.get("pivot") is None and "no l1 pivot" in span:
        return "none", None
    return "unavailable", None


def _rewrite_state(article, f):
    return _rewrite_info(article, f)[0]


def _unavailable_rewrite_copy(reason):
    if reason == "too few snapshots":
        return (
            "Too few snapshots for rewrite analysis",
            "The saved token corpus does not contain enough snapshots to run L1 for this article. "
            "This is insufficient coverage, not a finding that no rewrite occurred.",
        )
    if reason == "candidate artifact could not be materialized":
        return (
            "Before-and-after evidence is unavailable",
            "L1 found a candidate interval, but its exact revision text could not be exported. "
            "The candidate should not be interpreted without that evidence.",
        )
    if reason == "stale exact confirmation":
        return (
            "Rewrite analysis needs refresh",
            "The saved exact result does not match the current local corpus or detector thresholds. "
            "It is withheld until refreshed, rather than shown as a current finding.",
        )
    if reason and reason.startswith("loaded ") and reason.endswith(" expected snapshots"):
        return (
            "Rewrite analysis has incomplete source coverage",
            f"The exact rewrite detector {reason} from the public revision history. It withholds "
            "the result rather than treating gaps in readable history as evidence.",
        )
    return (
        "Rewrite analysis is not available",
        "No current rewrite result is available for this article. This is missing coverage, "
        "not a finding that no rewrite occurred.",
    )


def _lex_label(jsd):
    if jsd >= 0.35:
        return "wording changed a lot"
    if jsd >= 0.15:
        return "wording shifted clearly"
    if jsd >= 0.08:
        return "wording shifted a little"
    return None


def headline(article, f):
    """One-sentence plain-language lead for index cards and article intros."""
    bits = []
    confirmation = f.confirmations.get(article) or {}
    confirmed_episodes = confirmation.get("confirmed_episodes") or []
    top = None if confirmation else _top_pivot(article, f)
    if confirmed_episodes:
        strongest = max(confirmed_episodes, key=lambda episode: episode.get("pwr_mass") or 0)
        count = len(confirmed_episodes)
        drop = 100 * (strongest.get("durable_spine_drop") or 0)
        bits.append(
            f"{count} confirmed rewrite episode{'s' if count != 1 else ''}; "
            f"the largest by persistence-weighted mass had a {drop:.1f}% durable-spine drop"
        )
    elif top:
        pct = top.get("peak_pct")
        start = top.get("start") or "?"
        n = len((f.pivots.get(article) or {}).get("pivots") or [])
        confirmed = _pivot_status(top) == "confirmed"
        kind = "confirmed" if confirmed else "candidate"
        if n > 1:
            core = (
                f"{n} candidate rewrite windows stood out; the largest by persistence-weighted mass "
                f"began around {start} ({_pwr_read(pct)} at the peak)"
            )
        else:
            core = (
                f"Long-lived wording was substantially replaced around {start} "
                f"({kind} window; {_pwr_read(pct)} at the peak)"
            )
        bits.append(core)
    elif article in f.diffs:
        bits.append("Before-and-after comparison of two English versions")

    jsd = lexical_score(article, f)
    lab = _lex_label(jsd)
    if lab:
        if top or article in f.diffs:
            bits.append(lab)
        else:
            span = (f.lexical.get(article) or {}).get("span") or ""
            whole = "no L1 pivot" in span or "whole history" in span
            if whole and jsd >= 0.25:
                bits.append(
                    "The wording changed a lot over the full lifetime of the page "
                    "(no single overhaul date stood out)"
                )
            else:
                bits.append(lab[0].upper() + lab[1:])

    fact_counts = _fact_counts(f.factchecks.get(article) or {})
    n_tot = sum(fact_counts.values())
    n_contra = fact_counts.get("contradict", 0)
    n_differ = fact_counts.get("differ", 0)
    if n_contra:
        if n_contra == 1:
            bits.append(f"1 factual question out of {n_tot} contradicts across languages")
        else:
            bits.append(f"{n_contra} factual questions out of {n_tot} contradict across languages")
    if n_differ:
        if n_differ == 1:
            bits.append(f"1 question out of {n_tot} has a compatible detail difference")
        else:
            bits.append(f"{n_differ} questions out of {n_tot} have compatible detail differences")

    fr = f.framings.get(article) or {}
    divs = fr.get("divergences") or []
    if divs:
        if any(d.get("verdict") == "contradict" for d in divs):
            bits.append("language openings contradict each other on something important")
        else:
            bits.append("language openings emphasize different things")

    if not bits:
        src = f.sources.get(article)
        if src and (src.get("added") or src.get("dropped")):
            return "The mix of footnotes changed; no single large rewrite stood out."
        return "Only mild change shows up in the checks that were run."

    if len(bits) == 1:
        s = bits[0]
        return s if s[0].isupper() else s[0].upper() + s[1:] + "."
    head, second = bits[:2]
    if not head[0].isupper():
        head = head[0].upper() + head[1:]
    if not second[0].isupper():
        second = second[0].upper() + second[1:]
    return head.rstrip(".") + ". " + second.rstrip(".") + "."


# ---- pages ------------------------------------------------------------------
PAGE = _asset("templates/page.html")
FOOTER = _asset("templates/footer.html")

NAV_KEYS = ("findings", "about", "methodology", "glossary")
MERMAID_SCRIPT = (
    '<script src="https://cdn.jsdelivr.net/npm/mermaid@11.4.1/dist/mermaid.min.js" '
    'integrity="sha384-rbtjAdnIQE/aQJGEgXrVUlMibdfTSa4PQju4HDhN3sR2PmaKFzhEafuePsl9H/9I" '
    'crossorigin="anonymous"></script>'
)


def render_page(*, title, body, root="", path="index.html", description=None, active=None):
    """Fill the family page shell (meta, nav, footer)."""
    desc = description or (
        "WikiDrift measures Wikipedia article change and cross-edition disagreement from public data. "
        "A diagnostic tool from encyclopediae.org.")
    canon = f"{SITE_ORIGIN}/{path.lstrip('/')}" if path != "index.html" else f"{SITE_ORIGIN}/"
    nav = {k: ' class="active" aria-current="page"' if k == active else "" for k in NAV_KEYS}
    scripts = MERMAID_SCRIPT if 'class="language-mermaid"' in body else ""
    rendered = PAGE.format(
        title=title, description=esc(desc), canonical=canon, root=root, body=body,
        footer=FOOTER.format(root=root), scripts=scripts,
        nav_findings=nav["findings"], nav_about=nav["about"],
        nav_methodology=nav["methodology"], nav_glossary=nav["glossary"],
    )
    return "\n".join(line.rstrip() for line in rendered.splitlines()) + "\n"


def tabs(panels):
    """panels: list of (label, html, slug). First is active by default (Overview)."""
    bar = "".join(
        f'<button class="tab{" active" if i == 0 else ""}" role="tab" id="tab-{slug}" '
        f'data-t="{i}" data-slug="{slug}" aria-controls="panel-{slug}" '
        f'tabindex="{"0" if i == 0 else "-1"}" '
        f'aria-selected="{"true" if i == 0 else "false"}">{esc(label)}</button>'
        for i, (label, _, slug) in enumerate(panels))
    pans = "".join(
        f'<section class="panel{" active" if i == 0 else ""}" role="tabpanel" id="panel-{slug}" '
        f'aria-labelledby="tab-{slug}" data-p="{i}" data-slug="{slug}">{html}</section>'
        for i, (_, html, slug) in enumerate(panels))
    return f'<div class="tabs"><div class="tabbar" role="tablist" aria-label="Article findings">{bar}</div>{pans}</div>'


def wiki_en_url(article):
    return "https://en.wikipedia.org/wiki/" + urllib.parse.quote(article.replace(" ", "_"))


def _signal_cards(article, f):
    """Story-first cards for the Overview briefing."""
    cards = []
    confirmation = f.confirmations.get(article) or {}
    confirmed_episodes = confirmation.get("confirmed_episodes") or []
    top = None if confirmation else _top_pivot(article, f)
    pivs = [] if confirmation else (f.pivots.get(article) or {}).get("pivots") or []
    if confirmed_episodes:
        strongest = max(confirmed_episodes, key=lambda episode: episode.get("pwr_mass") or 0)
        drop = 100 * (strongest.get("durable_spine_drop") or 0)
        cards.append(
            f'<div class="signal-card hot">'
            f'<div class="signal-label">Confirmed rewrite episodes</div>'
            f'<div class="signal-value">{len(confirmed_episodes)} confirmed</div>'
            f'<p class="signal-note">Largest by persistence-weighted mass: {drop:.1f}% '
            f'durable-spine drop. See <a href="#diff">Rewrite</a> for exact revision receipts.</p></div>'
        )
    elif top:
        pct = top.get("peak_pct")
        status = _pivot_status(top)
        pct_s = _pwr_read(pct)
        n = len(pivs)
        if n > 1:
            windows = "".join(
                f'<li><a href="{esc(slugify(article))}.p{i}.html">'
                f'<b>{esc(p.get("start", "?"))} → {esc(p.get("end", "?"))}</b></a>'
                f'<span>{esc(_pwr_read(p.get("peak_pct")))}</span></li>'
                for i, p in enumerate(pivs)
            )
            cards.append(
                f'<div class="signal-card hot">'
                f'<div class="signal-label">Candidate rewrite windows</div>'
                f'<div class="signal-value">{n} candidate windows</div>'
                f'<ul class="signal-windows">{windows}</ul>'
                f'<p class="signal-note">Open a window for its before-and-after text, or see '
                f'<a href="#diff">Rewrite</a> for the full list.</p></div>'
            )
        else:
            cards.append(
                f'<div class="signal-card hot">'
                f'<div class="signal-label">{"Confirmed rewrite" if status == "confirmed" else "Candidate rewrite window"}</div>'
                f'<div class="signal-value">{esc(top.get("start", "?"))} → {esc(top.get("end", "?"))}</div>'
                f'<p class="signal-note">Peak interval: {esc(pct_s)}. '
                f'Open <a href="#diff">Rewrite</a> to read the old and new text.</p></div>'
            )
    elif article in f.diffs:
        d = f.diffs[article]
        when = (d.get("before") or {}).get("date") or "a past version"
        cards.append(
            f'<div class="signal-card hot">'
            f'<div class="signal-label">Version comparison</div>'
            f'<div class="signal-value">{esc(when)} → today</div>'
            f'<p class="signal-note">These two selected English versions are shown side by side. '
            f'See <a href="#diff">Rewrite</a>.</p></div>'
        )
    elif _rewrite_state(article, f) == "none":
        exact_rejection = confirmation.get("status") == "not_confirmed"
        cards.append(
            f'<div class="signal-card cool">'
            f'<div class="signal-label">Rewrite scan</div>'
            f'<div class="signal-value">No candidate window {"confirmed" if exact_rejection else "found"}</div>'
            f'<p class="signal-note">L1 ran on this article and '
            f'{"exact checking did not confirm a durable rewrite" if exact_rejection else "did not cross the candidate threshold"}. '
            f'This does not mean the article never changed.</p></div>'
        )
    else:
        unavailable_title, unavailable_note = _unavailable_rewrite_copy(_rewrite_info(article, f)[1])
        cards.append(
            f'<div class="signal-card cool">'
            f'<div class="signal-label">Rewrite</div>'
            f'<div class="signal-value">{esc(unavailable_title)}</div>'
            f'<p class="signal-note">{esc(unavailable_note)}</p></div>'
        )

    jsd = lexical_score(article, f)
    if article in f.lexical:
        lab = _lex_label(jsd) or "wording barely shifted"
        hot = jsd >= 0.15
        cards.append(
            f'<div class="signal-card {"hot" if hot else "cool"}">'
            f'<div class="signal-label">Wording</div>'
            f'<div class="signal-value">{esc(lab)}</div>'
            f'<p class="signal-note">Which words grew or shrank is listed under '
            f'<a href="#lexical">Vocabulary</a>.</p></div>'
        )

    src = f.sources.get(article)
    if src:
        n_add, n_drop = len(src.get("added") or []), len(src.get("dropped") or [])
        b, a = src.get("before") or {}, src.get("after") or {}
        cards.append(
            f'<div class="signal-card {"hot" if (n_add + n_drop) >= 10 else "cool"}">'
            f'<div class="signal-label">Footnotes</div>'
            f'<div class="signal-value">{n_add} sites grew · {n_drop} shrank</div>'
            f'<p class="signal-note">Different websites cited: {b.get("n_domains", "—")} → '
            f'{a.get("n_domains", "—")}. We do not rate those sites. '
            f'See <a href="#sources">Citations</a>.</p></div>'
        )

    fact_counts = _fact_counts(f.factchecks.get(article) or {})
    n_tot = sum(fact_counts.values())
    if n_tot:
        n_contra = fact_counts.get("contradict", 0)
        fact_val = _fact_read(fact_counts)
        cards.append(
            f'<div class="signal-card {"hot" if n_contra else "cool"}">'
            f'<div class="signal-label">Basic facts across languages</div>'
            f'<div class="signal-value">{esc(fact_val)}</div>'
            f'<p class="signal-note">{n_tot} configured questions checked in more than one language. '
            f'See <a href="#facts">Facts</a>.</p></div>'
        )

    fr = f.framings.get(article) or {}
    divs = fr.get("divergences") or []
    if divs:
        n_c = sum(1 for d in divs if d.get("verdict") == "contradict")
        cards.append(
            f'<div class="signal-card hot">'
            f'<div class="signal-label">How openings sound</div>'
            f'<div class="signal-value">{len(divs)} clear differences'
            f'{f" · {n_c} contradict" if n_c else ""}</div>'
            f'<p class="signal-note">Compared around the rewrite window. '
            f'See <a href="#framing">Framing</a>.</p></div>'
        )

    return f'<div class="signal-grid">{"".join(cards)}</div>' if cards else ""


def overview_section(article, f, layers):
    """First tab: briefing cards, Wikipedia link, context."""
    parts = [
        '<h2>Overview</h2>',
        '<p class="lead">Use this briefing to choose what to inspect. The tabs contain the underlying comparisons.</p>',
        f'<a class="wiki-link" href="{esc(wiki_en_url(article))}" target="_blank" rel="noopener">'
        f'Open the live English Wikipedia article ↗</a>',
        _signal_cards(article, f),
    ]
    l4 = f.l4.get(article)
    if l4:
        seed = esc(l4.get("seed") or "another article")
        parts.append(
            f'<div class="callout"><b>How this page turned up.</b> Large deletions on '
            f'<b>{seed}</b> pointed investigators here. This article was then checked on '
            f'<b>its own</b> history — being on that trail does not, by itself, mean anything is wrong.</div>'
        )
    ms = mscore_section(article, f.mscore)
    if ms:
        parts.append('<h2 class="subhead">Did editors fight over it?</h2>')
        parts.append(ms)
    prof = profile_line(f.profiles.get(article))
    if prof:
        parts.append('<h2 class="subhead">Who wrote today’s text?</h2>')
        parts.append(prof)
    have = [name for name, ok, _ in layers if ok]
    missing = [name for name, ok, _ in layers if not ok]
    if have:
        parts.append(
            '<p class="coverage-note"><b>Available evidence:</b> '
            + ", ".join(f"<b>{esc(n)}</b>" for n in have)
            + ".</p>"
        )
    if missing:
        parts.append(
            '<p class="coverage-note muted"><b>Not available in this export:</b> '
            + ", ".join(esc(n) for n in missing)
            + ". Missing coverage is not a negative finding.</p>"
        )
    return "".join(p for p in parts if p)


def missing_diff_section(state="unavailable", reason=None):
    if state == "none":
        return (
            '<h2>No candidate rewrite window was found</h2>'
            '<p class="missing-note">The L1 rewrite scan ran, but no interval crossed its candidate '
            'threshold. This is a completed negative result for that detector, not a claim that the '
            'article never changed.</p>'
            + _durable_spine_explanation()
            + _interval_profile_chart({
                "coarse_verdict": "HEALTHY",
                "status": "not_confirmed",
                "interval_profile": [],
                "evaluated_candidates": [],
            })
        )
    title, note = _unavailable_rewrite_copy(reason)
    return (
        f'<h2>{esc(title)}</h2><p class="missing-note">{esc(note)}</p>'
        + _durable_spine_explanation()
        + _interval_profile_chart({
            "coarse_verdict": "SKIP" if reason == "too few snapshots" else "UNAVAILABLE",
            "status": "unavailable",
            "reason": reason,
            "interval_profile": [],
            "evaluated_candidates": [],
        })
    )


def _confirmation_horizon_note(confirmation):
    horizon = confirmation.get("corpus_horizon") or {}
    if not horizon.get("snapshot_date"):
        return ""
    return (
        '<p class="coverage-note">Snapshot corpus through '
        f'<b>{esc(horizon["snapshot_date"])}</b> '
        f'(revision {esc(horizon.get("snapshot_revid") or "unknown")}).</p>'
    )


def _format_duration(duration_seconds):
    if not isinstance(duration_seconds, (int, float)) or duration_seconds < 0:
        return "unknown"
    if duration_seconds < 3600:
        return f"{int(duration_seconds) // 60} minutes"
    return f"{duration_seconds / 3600:.1f} hours"


def _revision_link(revid, timestamp, label):
    text = timestamp or revid
    return (
        f'<a href="{oldid("en", revid)}" target="_blank" rel="noopener" '
        f'aria-label="{esc(label)} exact revision: {esc(text)}">{esc(text)}</a>'
    )


def _confirmation_episode_row(episode, pivots=None, slug=None):
    before_revid = episode.get("before_revid")
    after_revid = episode.get("after_revid")
    before = _revision_link(before_revid, episode.get("before_timestamp"), "Before")
    after = _revision_link(after_revid, episode.get("after_timestamp"), "After")
    drop = 100 * (episode.get("durable_spine_drop") or 0)
    duration = _format_duration(episode.get("duration_seconds"))
    pivot_index = next((
        index for index, pivot in enumerate((pivots or {}).get("pivots") or [])
        if (pivot.get("before_rev"), pivot.get("after_rev")) == (before_revid, after_revid)
    ), None)
    evidence = (
        f'<a href="{slug}.p{pivot_index}.html" '
        f'aria-label="View redline for exact revisions {esc(before_revid)} to {esc(after_revid)}">'
        'View redline</a>'
        if slug and pivot_index is not None
        else '<span class="muted">Redline unavailable</span>'
    )
    return (
        f'<tr><td>{before}</td><td>{after}</td><td>{drop:.1f}% durable-spine drop</td>'
        f'<td>{int(episode.get("pwr_mass") or 0):,}</td><td>{esc(duration)}</td>'
        f'<td>{evidence}</td></tr>'
    )


def _candidate_decision(candidate, required_drop):
    decision = candidate.get("decision")
    reason = candidate.get("rejection_reason")
    drop = candidate.get("durable_spine_drop")
    if decision == "confirmed":
        if isinstance(drop, (int, float)):
            return f"Confirmed: {100 * drop:.1f}% durable-spine drop"
        return "Confirmed"
    if reason == "durable_spine_drop_below_threshold" and isinstance(drop, (int, float)):
        return (
            f'Rejected: {100 * drop:.1f}% durable-spine drop, '
            f'below the required {100 * required_drop:.1f}%'
        )
    if reason == "insufficient_revision_evidence":
        return "Rejected: insufficient revisions to resolve an exact pair"
    return f'Rejected: {str(reason or "exact threshold not met").replace("_", " ")}'


def _candidate_exact_pair(candidate, episodes):
    """Render a candidate's exact pair, enriching it from the matching confirmed episode."""
    candidate_window = (candidate.get("candidate_start"), candidate.get("candidate_end"))
    candidate_pair = (candidate.get("exact_before_revid"), candidate.get("exact_after_revid"))
    episode = next((
        item for item in episodes
        if (
            (candidate_window[0]
             and candidate_window == (item.get("candidate_start"), item.get("candidate_end")))
            or (candidate_pair[0]
                and candidate_pair == (item.get("before_revid"), item.get("after_revid")))
        )
    ), {})
    before_revid = candidate.get("exact_before_revid") or episode.get("before_revid")
    after_revid = candidate.get("exact_after_revid") or episode.get("after_revid")
    if not before_revid or not after_revid:
        return ""
    before_timestamp = candidate.get("exact_before_timestamp") or episode.get("before_timestamp")
    after_timestamp = candidate.get("exact_after_timestamp") or episode.get("after_timestamp")
    before = _revision_link(before_revid, before_timestamp, "Before")
    after = _revision_link(after_revid, after_timestamp, "After")
    duration = _format_duration(episode.get("duration_seconds"))
    duration_text = f" · {esc(duration)}" if duration != "unknown" else ""
    return f'<span class="candidate-pair">Exact pair: {before} → {after}{duration_text}</span>'


def _candidate_evaluations_receipt(confirmation, pivots=None, slug=None, episodes=None):
    candidates = confirmation.get("evaluated_candidates") or []
    if not candidates:
        return ""
    episodes = episodes or []
    required_drop = (confirmation.get("thresholds") or {}).get("confirm_drop", 0.2)
    pivot_indexes = {
        (pivot.get("start"), pivot.get("end")): index
        for index, pivot in enumerate((pivots or {}).get("pivots") or [])
    }
    rows = []
    for candidate in candidates:
        source = "rolling second pass" if candidate.get("source") == "rolling" else "coarse interval"
        window = (candidate.get("candidate_start"), candidate.get("candidate_end"))
        pivot_index = pivot_indexes.get(window)
        window_label = f'{candidate.get("candidate_start")} to {candidate.get("candidate_end")}'
        evidence = (
            f'<a href="{slug}.p{pivot_index}.html" '
            f'aria-label="View redline for candidate {esc(window_label)}">View redline</a>'
            if slug and pivot_index is not None
            else '<span class="muted">Redline unavailable</span>'
        )
        decision = esc(_candidate_decision(candidate, required_drop))
        exact_pair = _candidate_exact_pair(candidate, episodes)
        outcome = f'<span class="candidate-decision">{decision}</span>'
        if exact_pair:
            outcome += exact_pair
        rows.append(
            f'<tr><td>{esc(candidate.get("candidate_start"))} → '
            f'{esc(candidate.get("candidate_end"))}'
            f'<span class="candidate-evidence">{evidence}</span></td>'
            f'<td>{outcome}</td>'
            f'<td>{esc(source)} · {esc(_pwr_read(candidate.get("peak_pct")))} · '
            f'{int(candidate.get("pwr_mass") or 0):,} '
            f'{_glossary_term("pwr-mass", "PWR mass")}</td></tr>'
        )
    return (
        '<h3 id="candidate-outcomes-heading">Candidates and exact outcomes</h3>'
        '<p class="muted">Every coarse lead and its revision-level decision, including rejected '
        f'candidates. {_glossary_term("redline", "Redlines")} compare the full candidate window.</p>'
        '<div class="tablewrap"><table class="candidate-outcomes" '
        'aria-labelledby="candidate-outcomes-heading">'
        '<thead><tr><th scope="col">Candidate window</th>'
        '<th scope="col">Exact outcome</th><th scope="col">Coarse signal</th>'
        '</tr></thead>'
        f'<tbody>{"".join(rows)}</tbody></table></div>'
    )


def _analysis_stage(label, state, detail, glossary_term=None):
    rendered_label = _glossary_term(glossary_term, label) if glossary_term else esc(label)
    return (
        '<li><span class="analysis-stage">'
        f'{rendered_label}</span><strong>{esc(state)}</strong><span>{esc(detail)}</span></li>'
    )


def _rewrite_analysis_path(confirmation, interval_count):
    coarse_verdict = confirmation.get("coarse_verdict")
    source_state = confirmation.get("source_state") or {}
    coarse_state, coarse_detail = {
        "PIVOT?": (
            "Candidate signal found",
            "The coarse scan found a deletion-heavy window worth exact checking.",
        ),
        "CREEP?": (
            "Gradual-change signal found",
            "The coarse scan found accumulated replacement worth closer inspection.",
        ),
        "HEALTHY": (
            "No candidate signal",
            "No measured interval crossed the detector's candidate threshold.",
        ),
        "SKIP": (
            "Not enough snapshots",
            "The saved corpus could not support the coarse interval scan.",
        ),
        "UNAVAILABLE": (
            "Unavailable",
            "No current coarse result is available in this export.",
        ),
    }.get(coarse_verdict, (
        "Not published",
        "This artifact does not include the coarse detector decision.",
    ))
    if coarse_verdict == "SKIP" and source_state.get("source_status") == "partial":
        coarse_state = "No mature covered intervals"
        coarse_detail = (
            "Readable snapshots were scored descriptively, but no mature interval remained "
            "eligible for a detector decision."
        )

    if interval_count:
        interval_state = f"{interval_count} interval{'s' if interval_count != 1 else ''} scored"
        interval_detail = "The bars below show persistence-weighted wording loss for each interval."
    elif coarse_verdict == "SKIP":
        interval_state = "Not scored"
        interval_detail = "There were too few snapshots to calculate interval-level loss."
    elif "interval_profile" not in confirmation:
        interval_state = "Legacy receipt"
        interval_detail = "This result predates interval-level receipts and needs a detector refresh."
    else:
        interval_state = "No bars available"
        interval_detail = "No interval-level measurements are available in this export."

    status = confirmation.get("status")
    candidates = confirmation.get("evaluated_candidates") or []
    episodes = confirmation.get("confirmed_episodes") or []
    if status == "confirmed":
        exact_state = f"{len(episodes)} rewrite episode{'s' if len(episodes) != 1 else ''} confirmed"
        exact_detail = "Revision-level checking found the required durable wording replacement."
    elif status == "not_confirmed" and candidates:
        exact_state = "Candidates rejected"
        exact_detail = "Revision-level checking did not find the required durable replacement."
    elif status == "not_confirmed" and coarse_verdict == "HEALTHY":
        exact_state = "Not needed"
        exact_detail = "No coarse candidate was sent to revision-level checking."
    elif status == "not_confirmed":
        exact_state = "Completed, details unavailable"
        exact_detail = "The saved result is negative, but its candidate receipts were not published."
    elif status == "unavailable":
        exact_state = "Unavailable"
        exact_detail = "No current revision-level decision can be published."
    else:
        exact_state = "Not run"
        exact_detail = "No revision-level decision is available for this article."

    return (
        '<section class="analysis-path-wrap" aria-labelledby="analysis-path-title">'
        '<h4 id="analysis-path-title">How the detector reached this state</h4>'
        '<ol class="analysis-path">'
        f'{_analysis_stage("1 · Coarse scan", coarse_state, coarse_detail, "coarse-scan")}'
        f'{_analysis_stage("2 · Interval scoring", interval_state, interval_detail)}'
        f'{_analysis_stage("3 · Exact check", exact_state, exact_detail, "exact-check")}'
        '</ol></section>'
    )


def _interval_profile_chart(confirmation):
    intervals = confirmation.get("interval_profile") or []
    candidates = confirmation.get("evaluated_candidates") or []
    episodes = confirmation.get("confirmed_episodes") or []
    axis = (
        '<div class="drift-axis" aria-hidden="true"><span>0%</span>'
        '<span>25% candidate floor</span><span>50%</span><span>75%</span><span>100%</span></div>'
    )
    rows = []
    for interval in intervals:
        loss = float(interval.get("pwr_loss") or 0)
        gain = float(interval.get("pwr_gain") or 0)
        replacement = float(interval.get("replacement_candidate") or 0)
        confirmable = bool(interval.get("confirmable", interval.get("mature")))
        anomaly_types = interval.get("anomaly_types") or []
        eligible = interval.get("eligible", True) is not False
        interval_start = interval.get("start")
        interval_end = interval.get("end")
        candidate = next((
            item for item in candidates
            if item.get("candidate_start") and item.get("candidate_end")
            and interval_start and interval_end
            and item["candidate_start"] <= interval_start
            and interval_end <= item["candidate_end"]
        ), None)
        episode = next((
            item for item in episodes
            if item.get("candidate_start") and item.get("candidate_end")
            and interval_start and interval_end
            and item["candidate_start"] <= interval_start
            and interval_end <= item["candidate_end"]
        ), None)
        candidate_decision = candidate.get("decision") if candidate else (
            "confirmed" if episode else None
        )
        is_candidate = candidate is not None or episode is not None
        state = (
            "Excluded: missing source coverage" if not eligible
            else {
                "confirmed": "Confirmed candidate window",
                "rejected": "Rejected candidate window",
            }.get(candidate_decision, "Candidate: exact check pending") if is_candidate
            else "Descriptive anomaly: below exact-check floor"
            if anomaly_types and not confirmable
            else "Measured anomaly: exact check pending"
            if anomaly_types else "Measured: no anomaly threshold crossed"
        )
        classes = ["drift-row"]
        if is_candidate:
            classes.append("candidate")
            if candidate_decision in {"confirmed", "rejected"}:
                classes.append(f"candidate-{candidate_decision}")
        if not eligible:
            classes.append("excluded")
        if anomaly_types and not confirmable:
            classes.append("descriptive")
        metrics = [f"Loss {loss:.1f}%"]
        if "gain" in anomaly_types or gain:
            metrics.append(f"Gain {gain:.1f}%")
        if "replacement" in anomaly_types or replacement:
            metrics.append(f"Replacement lead {replacement:.1f}%")
        rows.append(
            f'<li class="{" ".join(classes)}">'
            f'<span class="drift-date">{esc(interval.get("end"))}</span>'
            '<span class="drift-track" aria-hidden="true">'
            f'<span class="drift-bar" style="width:{min(max(loss, 0), 100):.2f}%"></span></span>'
            f'<span class="drift-value">{esc(", ".join(metrics))}</span>'
            f'<span class="drift-mass" aria-label="'
            f'{int(interval.get("pwr_removed") or 0):,} removed, '
            f'{int(interval.get("pwr_added") or 0):,} added persistence-weighted units">'
            f'{int(interval.get("pwr_removed") or 0):,} removed · '
            f'{int(interval.get("pwr_added") or 0):,} added PWR</span>'
            f'<span class="drift-state">{esc(state)}</span></li>'
        )
    if rows:
        plot = (
            f'{axis}'
            f'<ul class="drift-plot">{"".join(rows)}</ul>'
        )
    else:
        if confirmation.get("coarse_verdict") == "SKIP":
            empty_detail = "Too few snapshots were available to calculate interval-level loss."
        elif "interval_profile" not in confirmation:
            empty_detail = (
                "This saved result predates interval-level receipts. Refresh the detector to publish "
                "measured bars."
            )
        else:
            empty_detail = "No interval-level measurements are available in this export."
        plot = (
            f'{axis}<ul class="drift-plot"><li class="drift-row drift-row-missing">'
            '<span class="drift-date">No interval</span>'
            '<span class="drift-track" aria-hidden="true"></span>'
            '<span class="drift-value">—</span><span class="drift-mass">—</span>'
            '<strong class="drift-state">Data missing: verdict unavailable</strong></li></ul>'
            f'<p class="drift-missing-reason">{esc(empty_detail)}</p>'
        )
    return (
        '<figure class="drift-profile" aria-labelledby="drift-profile-title">'
        '<figcaption><h3 id="drift-profile-title">'
        f'{_glossary_term("persistence-weighted-loss", "Persistence-weighted change")} by interval</h3>'
        '<p>Each row reports established wording lost, standing wording gained, and paired change as a '
        'replacement lead. The bar shows loss for continuity with earlier reports. '
        'Below-floor anomalies remain descriptive evidence but cannot receive exact confirmation. '
        'A coverage gap is shown descriptively but excluded from detector decisions. '
        'Candidate-window labels report the exact revision-level decision for the broader window '
        'containing that interval.</p></figcaption>'
        f'{_rewrite_analysis_path(confirmation, len(intervals))}{plot}'
        '</figure>'
    )


def _attribution_receipt(episode):
    attribution = episode.get("attribution")
    if not isinstance(attribution, dict):
        reason = episode.get("attribution_unavailable") or "not available"
        return (
            '<p class="coverage-note muted"><b>Exact-event attribution unavailable:</b> '
            f'{esc(reason)}.</p>'
        )
    removal_rows = attribution.get("removals_by_editor") or []
    replacement_rows = attribution.get("replacement_by_editor") or []
    details = [
        f'<b>{int(attribution.get("removed_tokens") or 0):,}</b> '
        f'{_glossary_term("token", "tokens")} removed',
        f'<b>{int(attribution.get("replacement_tokens") or 0):,}</b> surviving replacement tokens',
    ]
    if removal_rows and attribution.get("top_removal_share") is not None:
        details.append(
            f'<b>{esc(removal_rows[0].get("editor"))}</b> was associated with '
            f'<b>{100 * attribution["top_removal_share"]:.1f}%</b> of removals'
        )
    if replacement_rows and attribution.get("top_replacement_share") is not None:
        details.append(
            f'<b>{esc(replacement_rows[0].get("editor"))}</b> was the origin author of '
            f'<b>{100 * attribution["top_replacement_share"]:.1f}%</b> of surviving replacement text'
        )
    return (
        '<div class="evidence-receipt" role="note"><h3>Exact-event attribution: revisions '
        f'{esc(episode.get("before_revid"))} → {esc(episode.get("after_revid"))}</h3>'
        f'<p>{" · ".join(details)}.</p></div>'
    )


def _process_context_receipt(episode):
    context = episode.get("process_context")
    if not isinstance(context, dict):
        reason = episode.get("process_context_unavailable")
        if not reason:
            return ""
        return (
            '<p class="coverage-note muted"><b>Editorial process context unavailable:</b> '
            f'{esc(reason)}. Missing process evidence is not a negative finding.</p>'
        )

    activity = []
    for row in context.get("revision_activity") or []:
        section = f' · section “{esc(row["section"])}”' if row.get("section") else ""
        comment = f' · {esc(row["comment"])}' if row.get("comment") else ""
        activity.append(
            f'<li><a href="{esc(row.get("source_url"))}" target="_blank" rel="noopener">'
            f'revision {esc(row.get("revision_id"))}</a> · {esc(row.get("account"))}'
            f'{section}{comment}</li>'
        )
    restorations = []
    for row in context.get("revert_relationships") or []:
        target = row.get("restores_revision_id")
        target_text = f" restores the content state at revision {target}" if target else " has a revert tag"
        restorations.append(
            f'<li><a href="{esc(row.get("source_url"))}" target="_blank" rel="noopener">'
            f'revision {esc(row.get("revision_id"))}</a>{esc(target_text)}.</li>'
        )
    talk = []
    for row in context.get("talk_activity") or []:
        section = f' · section “{esc(row["section"])}”' if row.get("section") else ""
        talk.append(
            f'<li><a href="{esc(row.get("source_url"))}" target="_blank" rel="noopener">'
            f'talk revision {esc(row.get("revision_id"))}</a>{section}</li>'
        )
    operations = []
    for row in context.get("page_operations") or []:
        label = row.get("action") or row.get("type") or "page operation"
        operations.append(
            f'<li><a href="{esc(row.get("source_url"))}" target="_blank" rel="noopener">'
            f'{esc(label)}</a> · {esc(row.get("timestamp"))}</li>'
        )

    availability = context.get("availability") or {}
    talk_status = availability.get("talk_activity") or {}
    if talk_status.get("status") == "unavailable":
        talk_note = (
            '<p class="coverage-note muted"><b>Talk-page activity unavailable:</b> '
            f'{esc(talk_status.get("reason") or "retrieval unavailable")}. '
            'Missing discussion evidence is not evidence that no discussion occurred.</p>'
        )
    elif talk_status.get("status") == "not_observed":
        talk_note = (
            '<p class="coverage-note muted">No talk-page revisions were observed in the bounded '
            'retrieval window. This does not establish that no discussion occurred elsewhere.</p>'
        )
    else:
        talk_note = ""

    groups = []
    if activity:
        groups.append(f'<h4>Bounded revision activity</h4><ul>{"".join(activity)}</ul>')
    if restorations:
        groups.append(f'<h4>Restoration or revert alternatives</h4><ul>{"".join(restorations)}</ul>')
    if talk:
        groups.append(f'<h4>Talk-page activity</h4><ul>{"".join(talk)}</ul>')
    if operations:
        groups.append(f'<h4>Page operations</h4><ul>{"".join(operations)}</ul>')
    return (
        '<section class="process-context" aria-label="Editorial process context">'
        '<h3>Editorial process context</h3>'
        '<p class="muted">Descriptive public metadata for interpreting this event. It does not alter '
        'confirmation and does not establish motive, coordination, bias, or misconduct.</p>'
        f'{"".join(groups)}{talk_note}</section>'
    )


def _durable_spine_explanation():
    return (
        '<aside class="metric-definition" aria-labelledby="durable-spine-title">'
        '<h3 id="durable-spine-title">What is a '
        f'{_glossary_term("durable-spine-drop", "durable-spine drop")}?</h3>'
        f'<p>The <b>{_glossary_term("durable-spine", "durable spine")}</b> is the more persistent '
        'half of the wording present at the '
        'start of a candidate interval. The drop is the percentage-point decline in how much of '
        'that wording survives across the whole candidate window. The linked before-and-after '
        'revision pair locates the dominant step within that window; the percentage still measures '
        'the whole window. It measures established wording loss, not whether the resulting text is '
        'better, worse, more neutral, or less neutral.</p></aside>'
    )


def confirmation_section(confirmation, pivots=None, slug=None):
    """Render authoritative exact-confirmation episodes and revision receipts."""
    if confirmation.get("status") == "unavailable":
        source_state = confirmation.get("source_state") or {}
        reason = source_state.get("reason") if source_state.get("source_status") == "partial" else None
        reason = reason or confirmation.get("reason")
        if not reason and confirmation.get("coarse_verdict") == "SKIP":
            reason = "too few snapshots"
        title, note = _unavailable_rewrite_copy(reason)
        return (
            f'<h2>{esc(title)}</h2><p class="missing-note">{esc(note)}</p>'
            f'{_durable_spine_explanation()}{_interval_profile_chart(confirmation)}'
        )
    episodes = confirmation.get("confirmed_episodes") or []
    interval_chart = _interval_profile_chart(confirmation)
    candidate_receipt = _candidate_evaluations_receipt(confirmation, pivots, slug, episodes)
    if not episodes:
        return (
            '<h2>No candidate rewrite window was confirmed</h2>'
            '<p class="missing-note">The exact L1 check ran, but no candidate showed the required '
            'durable-spine drop. This is a completed negative result for that detector, not a claim '
            f'that the article never changed.</p>{_durable_spine_explanation()}'
            f'{interval_chart}{candidate_receipt}'
        )
    horizon_note = _confirmation_horizon_note(confirmation)
    legacy_episode_table = ""
    if not candidate_receipt:
        rows = "".join(
            _confirmation_episode_row(episode, pivots, slug)
            for episode in episodes
        )
        legacy_episode_table = (
            '<div class="tablewrap"><table><thead><tr><th scope="col">Before</th>'
            '<th scope="col">After</th><th scope="col">Durable change</th>'
            '<th scope="col">PWR mass</th><th scope="col">Duration</th>'
            '<th scope="col">Evidence</th></tr></thead>'
            f'<tbody>{rows}</tbody></table></div>'
        )
    receipts = "".join(
        _process_context_receipt(episode) + _attribution_receipt(episode)
        for episode in episodes
    )
    count = len(episodes)
    return (
        f'<h2>{count} confirmed {_glossary_term("rewrite-episode", "rewrite episode")}'
        f'{"s" if count != 1 else ""}</h2>'
        '<p>Events bounded by stable revisions where long-lived wording was substantially replaced.</p>'
        f'{horizon_note}{_durable_spine_explanation()}{interval_chart}'
        f'{candidate_receipt}{legacy_episode_table}{receipts}'
        '<p class="muted">Attribution describes public revision-history associations and origin authorship. '
        'It does not establish bias, motive, or misconduct.</p>'
    )


def sources_section(article, src):
    if not src:
        return ""
    b, a = src["before"], src["after"]
    n_add, n_drop = len(src.get("added") or []), len(src.get("dropped") or [])

    def mix(m):
        return ", ".join(f"{esc(k)} {v}%" for k, v in (m or {}).items()) or "—"

    def rows(items):
        out = "".join(
            f'<tr><td>{esc(x["domain"])}</td>'
            f'<td class="fromto">{x["from"]} → {x["to"]}</td></tr>'
            for x in items[:12]
        )
        return out or '<tr><td colspan="2" class="muted">none in top list</td></tr>'

    return (
        '<h2>How the footnotes changed</h2>'
        f'<p class="lead">{WHAT["sources"]}</p>'
        f'<p class="brief-sum">Looking at <b>{esc(src.get("span", ""))}</b>: '
        f'<b>{n_add}</b> websites were cited more (or newly), <b>{n_drop}</b> less (or dropped). '
        f'Number of footnotes: {b.get("refs", "—")} → {a.get("refs", "—")}. '
        f'Different websites: {b.get("n_domains", "—")} → {a.get("n_domains", "—")}.</p>'
        f'<p>Mix of citation types: <b>{mix(b.get("cite_mix"))}</b> → <b>{mix(a.get("cite_mix"))}</b>.</p>'
        '<div class="srcgrid">'
        '<div class="tablewrap"><table><thead><tr><th scope="col">cited more often</th>'
        f'<th scope="col">before → after</th></tr></thead><tbody>{rows(src.get("added") or [])}</tbody></table></div>'
        '<div class="tablewrap"><table><thead><tr><th scope="col">cited less often</th>'
        f'<th scope="col">before → after</th></tr></thead><tbody>{rows(src.get("dropped") or [])}</tbody></table></div>'
        '</div>'
        '<p class="muted">Counted from the article’s own footnotes. '
        'We list domains only — we do <b>not</b> call any source trustworthy or untrustworthy.</p>'
    )


# Markup / boilerplate tokens that rarely help a human read lexical drift.
_LEX_NOISE = {
    "thumb", "alt", "left", "right", "px", "upright", "frameless", "frame", "border",
    "cite", "ref", "refs", "nbsp", "mdash", "ndash", "amp", "quot", "lt", "gt",
    "category", "file", "image", "jpg", "jpeg", "png", "svg", "webp", "http", "https",
    "www", "com", "org", "html", "php", "also", "however", "while", "which", "their",
    "there", "these", "those", "would", "could", "should", "about", "after", "before",
    "between", "through", "during", "under", "over", "into", "from", "with", "without",
    "that", "this", "than", "then", "when", "where", "what", "were", "been", "have",
    "has", "had", "are", "was", "will", "can", "may", "might", "must", "shall",
}


def _filter_lex_terms(items, limit=12):
    out = []
    for x in items or []:
        term = (x.get("term") or "").strip().lower()
        if not term or term in _LEX_NOISE or len(term) < 3:
            continue
        if term.isdigit():
            continue
        out.append(x)
        if len(out) >= limit:
            break
    return out


def _term_chips(items, kind):
    """Visual before→after chips instead of a stats table."""
    if not items:
        return f'<p class="muted">No standout {kind} terms after filtering markup noise.</p>'
    chips = []
    for x in items:
        term = esc(x.get("term", ""))
        b, a = x.get("before", 0), x.get("after", 0)
        chips.append(
            f'<li class="term-chip {kind}"><span class="term">{term}</span>'
            f'<span class="term-count">{b} → {a}</span></li>'
        )
    return f'<ul class="term-list" aria-label="{kind} terms">{"".join(chips)}</ul>'


def _lexical_comparison_span(lex, confirmation):
    span = lex.get("span", "")
    if confirmation.get("status") != "not_confirmed" or lex.get("pivot") is None:
        return span
    before = lex.get("before") or {}
    after = lex.get("after") or {}
    interval = (
        f'{before["date"]} -> {after["date"]}'
        if before.get("date") and after.get("date")
        else "the selected before-and-after versions"
    )
    return (
        f'{interval} (around L1 candidate date {lex["pivot"]}; '
        'exact checking did not confirm a durable rewrite)'
    )


def lexical_section(lex, confirmation=None):
    if not lex:
        return ""
    confirmation = confirmation or {}
    over = _filter_lex_terms(lex.get("overrepresented_after_terms") or lex.get("gained_terms") or [])
    under = _filter_lex_terms(lex.get("underrepresented_after_terms") or lex.get("lost_terms") or [])
    b = lex.get("before") or {}
    a = lex.get("after") or {}
    try:
        jsd = float(lex.get("js_divergence") or 0)
    except (TypeError, ValueError):
        jsd = 0.0
    lab = _lex_label(jsd) or "small vocabulary change"
    span = _lexical_comparison_span(lex, confirmation)
    return (
        '<h2>Which words grew or shrank?</h2>'
        f'<p class="lead">{WHAT["lexical"]}</p>'
        f'<p class="brief-sum">Comparing <b>{esc(span)}</b>. '
        f'Rough word count: {b.get("tokens", 0):,} → {a.get("tokens", 0):,}. '
        f'Overall: <b>{esc(lab)}</b>.</p>'
        '<div class="srcgrid">'
        f'<div><h3 class="col-h">Used more afterward</h3>{_term_chips(over, "up")}</div>'
        f'<div><h3 class="col-h">Used less afterward</h3>{_term_chips(under, "down")}</div>'
        '</div>'
        '<p class="muted">These are the standout words, not a full dictionary of the article. '
        'Boring markup words are filtered out. Treat this as a hint, not a conclusion.</p>'
    )


def profile_line(prof):
    """Readable authorship / recency context. No editor names."""
    if not prof or prof.get("reason"):
        return ""
    conc = prof.get("top10_editor_share") or 0
    if conc >= 85:
        conc_note = "most of the current text comes from a small group of accounts"
    elif conc >= 70:
        conc_note = "a fairly small group of accounts wrote much of the current text"
    else:
        conc_note = "authorship is more spread out"
    horizon = prof.get("horizon")
    horizon_note = f' Snapshot data on this page runs through <b>{esc(horizon)}</b>.' if horizon else ""
    return (
        f'<p class="profile">Half of the wording still on the page is about '
        f'<b>{prof["median_age_yrs"]} years</b> old or newer. '
        f'<b>{prof["pct_recent"]}%</b> was written in the last '
        f'{prof["recent_years"]:.0f} years. '
        f'The ten most active accounts wrote <b>{conc}%</b> of what is there now '
        f'({conc_note}; {prof["distinct_editors"]} different accounts overall). '
        f'{horizon_note}'
        f'<span class="muted">This is background only — a small group of writers is common on specialist pages.</span></p>'
    )


def _framing_result_available(fr):
    """A file records an available comparison only after inference produced a result."""
    if not fr or fr.get("error"):
        return False
    calls = (fr.get("llm_usage") or {}).get("calls", 0)
    return bool(fr.get("divergences") or calls)


def framing_lite_block(fr):
    """Extra cross-language opening comparisons (when present)."""
    if not fr:
        return ""
    divs = fr.get("divergences") or []
    editions = fr.get("editions_compared") or []
    summary = fr.get("summary") or ""
    pivot = fr.get("pivot_window")
    mode = fr.get("mode") or ("pivot_informed_static" if pivot else "static")
    temporal = mode in ("candidate_relative", "pivot_relative")
    pivot_note = ""
    if pivot:
        window_label = "confirmed rewrite" if mode == "pivot_relative" else "L1 candidate window"
        pivot_note = (
            f'<p class="muted">Compared matched historical revisions around the {window_label} from '
            f'{esc(pivot.get("start", "?"))} to {esc(pivot.get("end", "?"))}.</p>'
        )
    head = (
        f'<h3 class="col-h">Cross-language lead comparison</h3>'
        f'<p class="lead">{WHAT["framing"]}</p>'
    )
    if summary:
        head += f'<p class="brief-sum">{esc(summary)}</p>'
    head += pivot_note
    if editions:
        head += f'<p class="muted">Languages compared: {esc(", ".join(editions))}.</p>'
    snapshots = fr.get("snapshots") or {}
    if temporal and snapshots:
        receipt_bits = []
        for lang in editions:
            before = (snapshots.get("before") or {}).get(lang) or {}
            after = (snapshots.get("after") or {}).get(lang) or {}
            links = []
            if before.get("revid"):
                links.append(
                    f'<a href="{oldid(lang, before["revid"])}" target="_blank" rel="noopener">'
                    f'{esc(lang)} before</a>'
                )
            if after.get("revid"):
                links.append(
                    f'<a href="{oldid(lang, after["revid"])}" target="_blank" rel="noopener">'
                    f'{esc(lang)} after</a>'
                )
            if links:
                receipt_bits.append(f'{esc(lang)}: {" / ".join(links)}')
        if receipt_bits:
            head += f'<p class="muted">Version receipts: {" · ".join(receipt_bits)}</p>'
    if not _framing_result_available(fr):
        return head + '<p class="muted">No comparison result is available for this run.</p>'
    if not divs:
        return head + '<p class="muted">No clear differences were recorded in this check.</p>'

    verdict_cls = {
        "contradict": "v-c", "differ": "v-d", "absent_en": "v-d",
        "absent_other": "v-i", "agree": "v-a",
    }
    v_plain = {
        "contradict": "contradict",
        "differ": "differ",
        "absent_en": "missing in English",
        "absent_other": "missing elsewhere",
        "agree": "agree",
    }
    rows = ""
    temporal_plain = {
        "english_moved_away": "English moved away",
        "english_converged": "English converged",
        "parallel_change": "parallel change",
        "difference_persisted": "difference persisted",
        "unclear": "unclear change",
    }
    temporal_cls = {
        "english_moved_away": "v-c",
        "english_converged": "v-a",
        "parallel_change": "v-i",
        "difference_persisted": "v-d",
        "unclear": "v-i",
    }
    for d in divs:
        v = d.get("verdict", "differ")
        if temporal:
            temporal_read = d.get("temporal_read", "unclear")
            cls = temporal_cls.get(temporal_read, "v-i")
            en_says = (
                f'<b>Before:</b> {esc(d.get("en_before") or "not stated")}<br>'
                f'<span class="muted">&ldquo;{esc(d.get("evidence_en_before") or "no quotation")}&rdquo;</span><br>'
                f'<b>After:</b> {esc(d.get("en_after") or "not stated")}<br>'
                f'<span class="muted">&ldquo;{esc(d.get("evidence_en_after") or "no quotation")}&rdquo;</span>'
            )
            other_says = (
                f'<b>Before:</b> {esc(d.get("other_before") or "not stated")}<br>'
                f'<span class="muted">&ldquo;{esc(d.get("evidence_other_before") or "no quotation")}&rdquo;</span><br>'
                f'<b>After:</b> {esc(d.get("other_after") or "not stated")}<br>'
                f'<span class="muted">&ldquo;{esc(d.get("evidence_other_after") or "no quotation")}&rdquo;</span>'
            )
        else:
            cls = verdict_cls.get(v, "v-i")
            other_says = esc(d.get("other_says") or "")
            ev_other = d.get("evidence_other")
            if ev_other:
                other_says += f'<br><span class="muted">&ldquo;{esc(ev_other)}&rdquo;</span>'
            en_says = esc(d.get("en_says") or "")
            ev_en = d.get("evidence_en")
            if ev_en:
                en_says += f'<br><span class="muted">&ldquo;{esc(ev_en)}&rdquo;</span>'
        eds = ", ".join(d.get("editions_differ") or [])
        comparison = temporal_plain.get(temporal_read, "unclear change") if temporal else v_plain.get(v, v)
        rows += (
            f'<tr><td>{esc(d.get("topic", ""))}</td>'
            f'<td><span class="badge {cls}">{esc(comparison)}</span></td>'
            f'<td>{en_says}</td><td>{other_says}</td>'
            f'<td class="muted" style="font-size:.82rem">{esc(eds)}</td></tr>'
        )
    table = (
        f'<div class="tablewrap"><table>'
        f'<thead><tr><th scope="col">topic</th><th scope="col">how they compare</th>'
        f'<th scope="col">English {"change" if temporal else "says"}</th>'
        f'<th scope="col">other language(s) {"change" if temporal else "say"}</th>'
        f'<th scope="col">languages</th></tr></thead>'
        f'<tbody>{rows}</tbody></table></div>'
    )
    return (
        head
          + ('' if temporal else '<p class="legend"><span class="badge v-c">contradict</span> opposite claims · '
              '<span class="badge v-d">differ</span> different emphasis · '
              '<span class="badge v-i">missing</span> only one side mentions it</p>')
        + table
        + '<p class="muted">Differences are invitations to read both sides — not a score of who is right.</p>'
    )


def framing_tab(article, f):
    """Render the current cross-language lead comparison when available."""
    fr = f.framings.get(article)
    if not _framing_result_available(fr):
        return ""
    return framing_lite_block(fr)


def _layer_flags(article, f):
    has_lex = article in f.lexical
    rewrite_state = _rewrite_state(article, f)
    has_src = article in f.sources
    framing = f.framings.get(article) or {}
    has_framing = _framing_result_available(framing)
    has_facts = bool(f.factchecks.get(article))
    has_rev = bool(_version_records(f.receipts.get(article), framing))
    return [
        ("Rewrite" if rewrite_state != "none" else "Rewrite (no candidate found)",
         rewrite_state != "unavailable", "not available"),
        ("Vocabulary", has_lex, "not available"),
        ("Citations", has_src, "not available"),
        ("Framing", has_framing, "not available"),
        ("Facts", has_facts, "not available"),
        ("Versions", has_rev, "not available"),
    ]


def article_page(article, f, categories=None):
    categories = categories or {}
    diff, pv = f.diffs.get(article), f.pivots.get(article)
    src = f.sources.get(article)
    lex = f.lexical.get(article)
    lead = headline(article, f)
    layers = _layer_flags(article, f)
    panels = [("Overview", overview_section(article, f, layers), "overview")]
    confirmation = f.confirmations.get(article)
    if confirmation:
        panels.append(("Rewrite", confirmation_section(confirmation, pv, slugify(article)), "diff"))
    elif pv:
        chart = _interval_profile_chart({"coarse_verdict": "PIVOT?", "status": "candidate"})
        panels.append((
            "Rewrite", render_pivots(pv, slugify(article)) + _durable_spine_explanation() + chart, "diff"
        ))
    elif diff:
        chart = _interval_profile_chart({"coarse_verdict": "PIVOT?", "status": "candidate"})
        panels.append(("Rewrite", diff_section(diff) + _durable_spine_explanation() + chart, "diff"))
    else:
        rewrite_state, rewrite_reason = _rewrite_info(article, f)
        panels.append(("Rewrite", missing_diff_section(rewrite_state, rewrite_reason), "diff"))
    if lex:
        panels.append(("Vocabulary", lexical_section(lex, confirmation), "lexical"))
    if src:
        panels.append(("Citations", sources_section(article, src), "sources"))
    framing_html = framing_tab(article, f)
    if framing_html:
        panels.append(("Framing", framing_html, "framing"))
    fcs = f.factchecks.get(article)
    if fcs:
        panels.append(("Facts", fact_section(article, fcs), "facts"))
    rec = f.receipts.get(article)
    if _version_records(rec, f.framings.get(article)):
        panels.append(("Versions", receipts_section(rec, f.framings.get(article)), "revisions"))
    cat = _category_for(article, categories)
    body = (
        f'<div class="page-intro"><p class="kicker">{esc(cat)}</p><h1>{esc(article)}</h1>'
        f'<p class="summary">{esc(lead)}</p>'
        '<p class="disclaimer">Something to inspect — not a judgment of bias or bad faith.</p></div>'
        f'<div class="workspace">{tabs(panels)}</div>'
    )
    return render_page(
        title=f"{article} — WikiDrift",
        body=body, root="../", path=f"article/{slugify(article)}.html",
        description=lead, active="findings",
    )


INDEX_JS = _asset("index.js")


def _fmt_pwr(n):
    """Compact PWR-mass label: 827,154 → '827k'."""
    return f"{n // 1000}k" if n >= 1000 else str(n)


def signal_badges(article, f, score=None):
    """Short plain cues for the findings list."""
    badges = []
    pwr = pivot_score(article, f)
    top = _top_pivot(article, f)
    if top:
        tier = "high" if pwr >= 200_000 else ("med" if pwr >= 50_000 else "")
        when = top.get("start") or "rewrite"
        pct = top.get("peak_pct")
        prefix = "rewrite" if _pivot_status(top) == "confirmed" else "candidate"
        label = f"{prefix} {when}" + (f" · {_pwr_read(pct)}" if pct is not None else "")
        cls = f"sig {tier}".strip()
        badges.append(f'<span class="{cls}">{esc(label)}</span>')
    elif article in f.diffs:
        badges.append('<span class="sig med">large rewrite</span>')

    jsd = lexical_score(article, f)
    lab = _lex_label(jsd)
    if lab and article in f.lexical:
        badges.append(f'<span class="sig lex">{esc(lab)}</span>')

    fact_counts = _fact_counts(f.factchecks.get(article) or {})
    n_contra = fact_counts.get("contradict", 0)
    n_differ = fact_counts.get("differ", 0)
    if n_contra:
        fact_lbl = "1 contradiction" if n_contra == 1 else f"{n_contra} contradictions"
        badges.append(f'<span class="sig fact">{fact_lbl}</span>')
    elif n_differ:
        fact_lbl = "1 detail differs" if n_differ == 1 else f"{n_differ} details differ"
        badges.append(f'<span class="sig fact">{fact_lbl}</span>')

    fr = f.framings.get(article) or {}
    if fr.get("divergences"):
        has_contradict = any(d.get("verdict") == "contradict" for d in fr["divergences"])
        badge_cls = "sig framing-c" if has_contradict else "sig framing"
        label = "openings contradict" if has_contradict else "openings differ"
        badges.append(f'<span class="{badge_cls}">{label}</span>')

    prof = f.profiles.get(article) or {}
    conc = prof.get("top10_editor_share") or 0
    if conc >= 85:
        badges.append('<span class="sig conc">few writers dominate</span>')
    elif conc >= 70:
        badges.append('<span class="sig conc">few main writers</span>')

    if article in f.l4:
        badges.append('<span class="sig">found via related edits</span>')
    return "".join(badges)


def lexical_score(article, f):
    lex = f.lexical.get(article) or {}
    try:
        return float(lex.get("js_divergence", 0) or 0)
    except Exception:
        return 0.0


def pivot_score(article, f):
    """Top PWR-mass across confirmed pivots for this article (0 if none)."""
    pv = f.pivots.get(article) or {}
    pivs = pv.get("pivots") or []
    try:
        return max((int(p.get("pwr_mass") or 0) for p in pivs), default=0)
    except Exception:
        return 0


def index_page(articles, f, categories=None):
    categories = categories or {}
    cats = sorted({_category_for(a, categories) for a in articles})
    chips = (
        '<button type="button" class="fchip active" data-cat="all" aria-pressed="true">All</button>'
        + "".join(
            f'<button type="button" class="fchip" data-cat="{esc(c)}" aria-pressed="false">{esc(c)}</button>'
            for c in cats
        )
    )
    rows = []
    for a in articles:
        h = headline(a, f)
        cat = _category_for(a, categories)
        lex_sc = lexical_score(a, f)
        pwr_sc = pivot_score(a, f)
        badges = signal_badges(a, f)
        meta_html = f'<div class="f-meta">{badges}</div>' if badges else '<div class="f-meta"></div>'
        search_blob = " ".join([
            a, h, cat,
            "rewrite" if pwr_sc or a in f.diffs else "",
            "vocabulary" if a in f.lexical else "",
            "facts" if f.factchecks.get(a) else "",
            "framing" if _framing_result_available(f.framings.get(a)) else "",
        ]).lower()
        rows.append(
            f'<a class="finding" href="article/{slugify(a)}.html" data-cat="{esc(cat)}" '
            f'data-title="{esc(a.lower())}" data-score="{pwr_sc or lex_sc}" '
            f'data-lex="{lex_sc}" data-pwr="{pwr_sc}" '
            f'data-text="{esc(search_blob)}">'
            f'<div class="f-head"><span class="kicker">{esc(cat)}</span>'
            f'<h2>{esc(a)}</h2></div>'
            f'<div class="f-body"><p>{esc(h)}</p>{meta_html}</div>'
            f'<span class="f-go" aria-hidden="true">→</span></a>'
        )
    intro = f'<div class="page-intro">{FINDINGS_BODY}</div>'
    controls = (
        '<div class="controls"><input id="q" class="search" type="search" '
        'placeholder="Search by title…" aria-label="Search findings">'
        f'<div class="filters" role="group" aria-label="Filter by topic">{chips}</div>'
        '<label class="sortlab">Sort <select id="sort" class="sortsel" aria-label="Sort findings">'
        '<option value="pwr" selected>Largest rewrite first</option>'
        '<option value="lex">Biggest wording shift</option>'
        '<option value="az">A–Z</option>'
        '<option value="cat">Topic</option></select></label>'
        '<span id="count" class="count" role="status" aria-live="polite"></span></div>'
    )
    body = (
        intro + '<div class="workspace">' + controls
        + f'<div class="findings">{"".join(rows)}</div>'
        + '<p id="empty" hidden>No pages match this filter.</p>'
        + '<nav class="pager" id="pager" aria-label="Findings pages"></nav></div>' + INDEX_JS
    )
    return render_page(
        title="Findings — WikiDrift",
        body=body, path="findings.html",
        description="Browse WikiDrift findings: how Wikipedia articles changed, and how languages differ.",
        active="findings",
    )


def _unlink_unpublished_article_links(body, articles):
    published = {f"{slugify(article)}.html" for article in articles}

    def retain_or_unlink(match):
        target_path = urllib.parse.unquote(urllib.parse.urlsplit(match.group("target")).path)
        target_name = target_path.rsplit("/", 1)[-1]
        return match.group(0) if target_name in published else match.group("label")

    pattern = r'<a\b[^>]*\bhref="article/(?P<target>[^"]+)"[^>]*>(?P<label>.*?)</a>'
    return re.sub(pattern, retain_or_unlink, body)


def simple_page(title, body, active, path=None):
    path = path or (f"{active}.html" if active != "about" else "index.html")
    page_title = {
        "about": "About WikiDrift",
        "methodology": "How WikiDrift works",
        "glossary": "Glossary — WikiDrift",
    }.get(active, f"{title} — WikiDrift")
    desc = {
        "about": "What WikiDrift is: a plain look at how Wikipedia articles change over time.",
        "methodology": "How WikiDrift measures rewrites and language differences — in plain language.",
        "glossary": "Glossary of terms for WikiDrift pages.",
    }.get(active, f"{title} — WikiDrift.")
    return render_page(
        title=page_title,
        body=f'<div class="prose">{body}</div>',
        path=path,
        description=desc,
        active=active,
    )


def trust_report_payload(report):
    """Return the build trust report with corpus-level publication counts."""
    withheld = report.get("withheld") or []
    counts = {
        "published": len(report.get("published") or []),
        "withheld": len(withheld),
        "quarantined": 0,
        "unstable": 0,
        "stale": 0,
        "legacy_incompatible": 0,
    }
    for item in withheld:
        status = item.get("status")
        if status in counts:
            counts[status] += 1
    return {"counts": counts, **report}


def trust_report_page(report):
    """Render specific withholding reasons for build operators and researchers."""
    payload = trust_report_payload(report)
    counts = payload["counts"]
    rows = "".join(
        "<tr>"
        f"<td>{esc(item['article'])}</td><td>{esc(item['artifact_kind'])}</td>"
        f"<td>{esc(item['status'])}</td><td>{esc(item['reason'])}</td>"
        f"<td>{esc(item['path'])}</td></tr>"
        for item in payload["withheld"]
    ) or '<tr><td colspan="5">No artifacts withheld.</td></tr>'
    body = (
        "<h1>Corpus trust report</h1>"
        f"<p>{counts['published']} published · {counts['withheld']} withheld · "
        f"{counts['quarantined']} quarantined · {counts['unstable']} unstable · "
        f"{counts['stale']} stale · {counts['legacy_incompatible']} legacy-incompatible</p>"
        "<div class=\"table-wrap\"><table><thead><tr><th>Article</th><th>Artifact</th>"
        "<th>State</th><th>Reason</th><th>File</th></tr></thead>"
        f"<tbody>{rows}</tbody></table></div>"
    )
    return simple_page("Corpus trust report", body, None, path="trust-report.html")


ABOUT_BODY = _md_asset("about")
FINDINGS_BODY = _md_asset("findings")
SUMMARY_BODY = _md_asset("summary")

GLOSSARY_BODY = _md_asset("glossary")

METHODOLOGY_BODY = _md_asset("methodology")


CSS = _asset("style.css")


def _parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Build static WikiDrift site")
    parser.add_argument(
        "--llm-categories",
        action="store_true",
        help="Use LLM to categorize topics for index filters (cached in findings)",
    )
    parser.add_argument(
        "--refresh-categories",
        action="store_true",
        help="Refresh cached LLM categories for all topics (implies --llm-categories)",
    )
    parser.add_argument("--provider", default=None, help="LLM provider override for category classification")
    parser.add_argument("--model", default=None, help="LLM model override for category classification")
    parser.add_argument("--base-url", dest="base_url", default=None,
                        help="LLM base URL override for category classification")
    parser.add_argument("--category-cache", default=str(CATEGORY_CACHE),
                        help="Path to persisted topic-category cache JSON")
    return parser.parse_args(argv)


def main(argv=None):
    args = _parse_args(argv)
    f = gather()
    articles = f.articles()
    use_llm_categories = args.llm_categories or args.refresh_categories
    categories = resolve_categories(
        articles,
        use_llm=use_llm_categories,
        refresh=args.refresh_categories,
        cache_path=pathlib.Path(args.category_cache),
        provider=args.provider,
        model=args.model,
        base_url=args.base_url,
    )
    SITE.mkdir(parents=True, exist_ok=True)
    (SITE / "article").mkdir(exist_ok=True)
    for stale in (SITE / "article").glob("*.html"):
        stale.unlink()
    (SITE / "style.css").write_text(CSS, encoding="utf-8")
    (SITE / "site.js").write_text(_asset("site.js"), encoding="utf-8")
    (SITE / "CNAME").write_text(CUSTOM_DOMAIN + "\n", encoding="utf-8")
    (SITE / ".nojekyll").write_text("", encoding="utf-8")
    # Homepage is About; findings live at findings.html. about.html kept as an alias for old links.
    about_home = simple_page("About", ABOUT_BODY, "about", path="index.html")
    (SITE / "index.html").write_text(about_home, encoding="utf-8")
    (SITE / "about.html").write_text(simple_page("About", ABOUT_BODY, "about", path="about.html"), encoding="utf-8")
    (SITE / "findings.html").write_text(index_page(articles, f, categories), encoding="utf-8")
    summary_body = _unlink_unpublished_article_links(SUMMARY_BODY, articles)
    (SITE / "summary.html").write_text(
        simple_page("Summary of findings", summary_body, None, path="summary.html"), encoding="utf-8")
    (SITE / "methodology.html").write_text(
        simple_page("How it works", METHODOLOGY_BODY, "methodology"), encoding="utf-8")
    # Still published at glossary.html so old bookmarks work; page is "Glossary."
    (SITE / "glossary.html").write_text(
        simple_page("Glossary", GLOSSARY_BODY, "glossary"), encoding="utf-8")
    trust_payload = trust_report_payload(f.trust_report)
    (SITE / "trust-report.json").write_text(
        json.dumps(trust_payload, indent=2, sort_keys=True), encoding="utf-8"
    )
    (SITE / "trust-report.html").write_text(
        trust_report_page(f.trust_report), encoding="utf-8"
    )
    for a in articles:
        (SITE / "article" / f"{slugify(a)}.html").write_text(article_page(a, f, categories), encoding="utf-8")
        if a in f.pivots:
            for i, p in enumerate(f.pivots[a]["pivots"]):
                (SITE / "article" / f"{slugify(a)}.p{i}.html").write_text(pivot_page(a, p, i), encoding="utf-8")
    print(f"built {len(articles)} article pages + home(about)/findings/summary/methodology/glossary -> {SITE}")
    print(f"CNAME {CUSTOM_DOMAIN}")
    for a in articles:
        extras = "".join(t for t, has in (("P", a in f.pivots), ("D", a in f.diffs), ("B", a in f.blames),
                                           ("4", a in f.l4)) if has)
        print(f"  - {a}{' [' + extras + ']' if extras else ''}")


if __name__ == "__main__":
    main()
