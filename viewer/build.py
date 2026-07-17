"""viewer/build.py — static findings-site generator for GitHub Pages.

Reads frozen findings JSON (no tool, no API, no keys) and renders a static site into `docs/`.
Family chrome matches encyclopediae.org (Source Sans, light header, dark footer).

Run:    python viewer/build.py
Deploy: GitHub Pages serves `/docs`; CNAME = wikidrift.encyclopediae.org
"""
import argparse
import difflib
import html
import json
import pathlib
import re
import urllib.parse
from collections import Counter
from dataclasses import dataclass, field

import markdown as _md

ROOT = pathlib.Path(__file__).resolve().parents[1]
FIND = ROOT / ".planning" / "spikes" / "data" / "findings"
DATA = pathlib.Path(__file__).resolve().parent / "data"
SITE = ROOT / "docs"
CUSTOM_DOMAIN = "wikidrift.encyclopediae.org"
SITE_ORIGIN = f"https://{CUSTOM_DOMAIN}"
EXCLUDE_ARTICLES = {"Demo Topic"}  # test fixtures — never ship

VIEWER = pathlib.Path(__file__).resolve().parent


def _asset(rel):
    """Read a static template/asset (HTML/CSS/JS) that lives beside build.py, verbatim."""
    return (VIEWER / rel).read_text(encoding="utf-8")


def _md_asset(stem):
    """Compile a Markdown template to HTML. Raw HTML blocks pass through unchanged."""
    text = (VIEWER / f"templates/{stem}.md").read_text(encoding="utf-8")
    return _md.markdown(text, extensions=["extra"])

# Editor tints for the (opt-in) blame overlay — light backgrounds, dark text (AA-safe).
BLAME_PALETTE = ["#f6dede", "#dde6f4", "#dfeede", "#f4eccf", "#e7ddf2", "#d5ecec",
                 "#f4e2cf", "#e4e4e6", "#efdde8", "#dcecdf"]
# Stance / verdict rendered as CSS classes — kept for any future L5 re-integration.
SCLASS = {"critical": "c", "sympathetic": "s", "neutral": "n", "absent": "a"}
VCLASS = {"contradict": "c", "differ": "d", "agree": "a", "insufficient": "i"}

# Short section intros (article pages). No glossary required — explain in place.
WHAT = {
    "diff": 'The biggest overhaul windows in the English article. Open one to read what was removed '
            'and what replaced it. A large rewrite means the text changed a lot — not that someone '
            'did something wrong.',
    "blame": 'Who introduced each part of the current opening paragraph. Each color is one Wikipedia account.',
    "sources": 'How the article\'s own footnotes changed across the rewrite: which websites and books '
               'were cited more or less. We only show the mix — we do <b>not</b> rate sources as good or bad.',
    "lexical": 'Words that became more common or less common after the rewrite. Handy for noticing a '
               'shift in topic or tone — not a score of bias.',
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
    # Controls / cross-domain (mostly off-site, kept for when they gain findings)
    "Photosynthesis": "Science (control)", "Water": "Science (control)", "Chess": "Science (control)",
    "Brontosaurus": "Science (control)", "Abortion": "Cross-domain", "Climate change": "Cross-domain",
}
DEFAULT_CATEGORY = "Other"
CATEGORY_CACHE = FIND / "topic_categories.json"
CATEGORY_OPTIONS = [
    "Israel–Palestine",
    "Holocaust in Poland",
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
        if isinstance(k, str) and isinstance(v, str):
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
    categories.update({a: cache[a] for a in articles if a in cache})
    needed = [a for a in articles if refresh or a not in cache]
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

    l4: dict = field(default_factory=dict)

    def articles(self):
        """Every public article with a renderable finding (excludes test fixtures)."""
        names = (set(self.pivots) | set(self.diffs) | set(self.lexical) | set(self.sources) | set(self.profiles))
        return sorted(a for a in names if a not in EXCLUDE_ARTICLES)


def gather():
    receipts, stances, factchecks, sources, lexical, profiles = {}, {}, {}, {}, {}, {}
    diver = {"static": {}, "pivot_relative": {}}
    mscore, l4map = {}, {}
    if FIND.exists():
        for f in FIND.glob("*.receipts.json"):
            d = load(f)
            if d and d.get("article") not in EXCLUDE_ARTICLES:
                receipts[d["article"]] = d
        for f in FIND.glob("*.stance.json"):
            d = load(f)
            if d and d.get("article") not in EXCLUDE_ARTICLES:
                stances[d["article"]] = d
        for f in FIND.glob("*.factcheck.json"):
            d = load(f)
            if d and d.get("article") not in EXCLUDE_ARTICLES:
                label = "now" if not d.get("asof") else d["asof"][:10]
                factchecks.setdefault(d["article"], {})[label] = d
        for f in FIND.glob("*.sources.json"):
            d = load(f)
            if d and d.get("article") not in EXCLUDE_ARTICLES:
                sources[d["article"]] = d
        for f in FIND.glob("*.lexical.json"):
            d = load(f)
            if d and d.get("article") not in EXCLUDE_ARTICLES:
                lexical[d["article"]] = d
        for f in FIND.glob("*.profile.json"):
            d = load(f)
            if d and d.get("article") not in EXCLUDE_ARTICLES:
                profiles[d["article"]] = d
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
    diffs, blames, pivots, framings = {}, {}, {}, {}
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
        for f in FIND.glob("*.framing.json"):
            d = load(f)
            if d and d.get("article"):
                framings[d["article"]] = d
    return Findings(receipts=receipts, stances=stances, factchecks=factchecks, diver=diver, mscore=mscore,
                    diffs=diffs, blames=blames, pivots=pivots, sources=sources, lexical=lexical,
                    profiles=profiles, framings=framings, l4=l4map)


# ---- shared fragments -------------------------------------------------------
def oldid(lang, revid):
    # esc() both fields: lang/revid are structural (a Wikidata site code + an int) in normal runs, but a
    # findings file is the same untrusted-input boundary as the content fields, so don't skip escaping here.
    return f"https://{esc(lang)}.wikipedia.org/w/index.php?oldid={esc(revid)}"


def receipts_section(rec):
    rows = []
    for lang, e in rec.get("editions", {}).items():
        if not e.get("present"):
            continue
        link = (
            f'<a href="{oldid(lang, e["revid"])}" target="_blank" rel="noopener">'
            f'open version {esc(e["revid"])}</a>'
        )
        rows.append(
            f"<tr><td><b>{esc(lang)}</b></td><td>{esc(e['title'])}</td>"
            f"<td>{link}</td><td>{esc(e.get('timestamp', ''))}</td>"
            f"<td>{e.get('prose_chars', 0):,}</td></tr>"
        )
    qid = esc(rec.get("qid", ""))
    return (
        '<h2>Versions we used</h2>'
        '<p class="lead">These are the exact public Wikipedia versions behind the checks on this page. '
        'Open any link to read the original. '
        'Wikidata item: <a href="https://www.wikidata.org/wiki/' + qid + '" target="_blank" '
        f'rel="noopener">{qid}</a>.</p>'
        '<div class="tablewrap"><table><thead><tr>'
        '<th scope="col">language</th><th scope="col">article title</th>'
        '<th scope="col">version</th><th scope="col">when</th>'
        '<th scope="col">length (chars)</th></tr></thead>'
        f'<tbody>{"".join(rows)}</tbody></table></div>'
    )


def stance_grid(st):
    langs, ents = st["langs"], st["entities"]
    head = "".join(f'<th scope="col">{esc(l)}</th>' for l in langs)
    rows = []
    eid = 0
    for e in ents:
        cells = []
        ev_rows = []
        for l in langs:
            r = st["editions"][l]["lead"].get(e) or {}
            s = r.get("stance", "absent")
            s_label = {
                "critical": "more critical",
                "sympathetic": "more sympathetic",
                "neutral": "neutral",
                "absent": "not mentioned",
            }.get(s, s)
            npov = "!" if r.get("npov_departure") else ""
            quote = (r.get("evidence") or "").strip()
            eid += 1
            cid = f"ev{eid}"
            if quote:
                cells.append(
                    f'<td class="sc-{SCLASS.get(s, "a")}" style="padding:0">'
                    f'<button type="button" class="cell-ev sc-{SCLASS.get(s, "a")}" '
                    f'aria-expanded="false" aria-controls="{cid}" '
                    f'aria-label="Show evidence for {esc(e)} in {esc(l)}: {esc(s_label)}">'
                    f'{esc(s_label)}{npov}</button></td>')
                ev_rows.append(
                    f'<tr class="ev-row" id="{cid}" hidden><td colspan="{len(langs)+1}" class="ev-panel">'
                    f'<b>{esc(l)} · {esc(e)}</b> — {esc(quote)}</td></tr>')
            else:
                cells.append(
                    f'<td class="cell sc-{SCLASS.get(s, "a")}">{esc(s_label)}{npov}</td>'
                )
        rows.append(f'<tr><th scope="row">{esc(e)}</th>{"".join(cells)}</tr>')
        rows.extend(ev_rows)
    legend = (
        '<span class="chip sc-c">more critical</span>'
        '<span class="chip sc-n">neutral</span>'
        '<span class="chip sc-s">more sympathetic</span>'
        '<span class="chip sc-a">not mentioned</span>'
    )
    return (
        f'<div class="tablewrap"><table class="grid"><thead><tr><th></th>{head}</tr></thead>'
        f'<tbody>{"".join(rows)}</tbody></table></div>'
        f'<p class="legend">{legend} '
        f'<span class="muted">A “!” means the opening clearly leans away from neutral. '
        f'Click a cell for the short quote.</span></p>'
    )


def stance_section(st, diver=None, article=None):
    """How language openings frame the topic."""
    if not st:
        return ""
    parts = [
        '<h2>How different languages open the topic</h2>',
        f'<p class="lead">{WHAT["stance"]}</p>',
    ]
    diver = diver or {}
    if article:
        stat = diver.get("static", {}).get(article)
        if stat:
            try:
                d = stat["variants"]["lead"]["divergence"]
                word = (
                    "mostly line up"
                    if d < 0.4
                    else ("differ somewhat" if d < 1.2 else "differ a lot")
                )
                parts.append(
                    f'<p>Overall, the openings <b>{word}</b> across languages.</p>'
                )
            except (KeyError, TypeError):
                pass
    parts.append(stance_grid(st))
    if article:
        pr = diver.get("pivot_relative", {}).get(article)
        if pr:
            read = pr.get("read")
            if read == "PEELED AWAY":
                msg = (
                    "Before the big English rewrite, the languages mostly agreed. Afterward, "
                    "English moved away from the others. That pattern is worth reading carefully — "
                    "it is still not proof of bad intent."
                )
            elif read == "no net change":
                msg = (
                    "The languages already disagreed by about the same amount before and after "
                    "the rewrite, so the gap may be older than that one overhaul."
                )
            else:
                msg = "Across the rewrite, the languages moved closer together."
            parts.append(f'<div class="callout"><b>Around the rewrite.</b> {msg}</div>')
    return "".join(parts)


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


def render_pivots(pv, slug):
    pivs = pv.get("pivots") or []
    items = []
    for i, p in enumerate(pivs):
        pct = p.get("peak_pct")
        if pct is not None:
            pct_s = f"about {pct:.0f}% of the article rewritten"
        else:
            pct_s = "major rewrite"
        items.append(
            f'<a class="pv-link" href="{slug}.p{i}.html">'
            f'<span><b>{esc(p["start"])} → {esc(p["end"])}</b>'
            f'<span class="muted"> · {esc(pct_s)}</span></span>'
            f'<span class="f-go" aria-hidden="true">→</span></a>'
        )
    n = len(pivs)
    return (
        f'<h2>Which candidate rewrite windows stood out?</h2>'
        f'<p class="lead">{WHAT["diff"]}</p>'
        f'<p class="brief-sum">The coarse PWR scan found <b>{n}</b> candidate window{"s" if n != 1 else ""}. '
        f'Open one to read the old wording next to the new wording.</p>'
        f'<div class="pvlinks">{"".join(items)}</div>'
    )


def pivot_page(article, p, i):
    slug = slugify(article)
    status = _pivot_status(p)
    title = "Confirmed rewrite" if status == "confirmed" else "Candidate rewrite"
    body = (
        f'<div class="page-intro"><p class="kicker"><a href="{slug}.html">← {esc(article)}</a></p>'
        f'<h1>{title} · {esc(p["start"])} → {esc(p["end"])}</h1>'
        f'<p class="summary">The peak interval measured {_pwr_read(p.get("peak_pct"))}. '
        f'Read it like tracked changes: <del>struck-out text</del> was removed; '
        f'<ins>highlighted text</ins> was added (color hints which account added it).</p>'
        f'<p class="disclaimer">{"Binary-search confirmed" if status == "confirmed" else "Coarse PWR candidate; not binary-search confirmed"}. '
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


def _lead_divergence(article, f):
    """Cross-edition lead stance spread, or None when that comparison was not computed."""
    try:
        return float(f.diver["static"][article]["variants"]["lead"]["divergence"])
    except (KeyError, TypeError, ValueError):
        return None


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


def _rewrite_state(article, f):
    """Return finding, none, or unavailable without inferring a negative result from missing files."""
    if article in f.pivots or article in f.diffs:
        return "finding"
    lexical = f.lexical.get(article) or {}
    span = str(lexical.get("span") or "").lower()
    if lexical.get("pivot") is None and "no l1 pivot" in span:
        return "none"
    return "unavailable"


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
    top = _top_pivot(article, f)
    if top:
        pct = top.get("peak_pct")
        start = top.get("start") or "?"
        n = len((f.pivots.get(article) or {}).get("pivots") or [])
        confirmed = _pivot_status(top) == "confirmed"
        kind = "confirmed" if confirmed else "candidate"
        core = (
            f"Long-lived wording was substantially replaced around {start} "
            f"({kind} window; {_pwr_read(pct)} at the peak)"
        )
        if n > 1:
            core += f" — one of {n} candidate windows"
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

    lead_div = _lead_divergence(article, f)
    if lead_div is not None and lead_div >= 0.4 and not divs:
        bits.append("language openings treat the topic differently")
    elif lead_div is not None and lead_div < 0.4 and not bits:
        bits.append("Compared language openings mostly line up")

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


def render_page(*, title, body, root="", path="index.html", description=None, active=None):
    """Fill the family page shell (meta, nav, footer)."""
    desc = description or (
        "WikiDrift measures Wikipedia article change and cross-edition disagreement from public data. "
        "A diagnostic tool from encyclopediae.org.")
    canon = f"{SITE_ORIGIN}/{path.lstrip('/')}" if path != "index.html" else f"{SITE_ORIGIN}/"
    nav = {k: ' class="active" aria-current="page"' if k == active else "" for k in NAV_KEYS}
    rendered = PAGE.format(
        title=title, description=esc(desc), canonical=canon, root=root, body=body,
        footer=FOOTER.format(root=root),
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
    top = _top_pivot(article, f)
    pivs = (f.pivots.get(article) or {}).get("pivots") or []
    if top:
        pct = top.get("peak_pct")
        status = _pivot_status(top)
        pct_s = _pwr_read(pct)
        n = len(pivs)
        extra = f" ({n} candidate windows found in total)" if n > 1 else ""
        cards.append(
            f'<div class="signal-card hot">'
            f'<div class="signal-label">{"Confirmed rewrite" if status == "confirmed" else "Candidate rewrite window"}</div>'
            f'<div class="signal-value">{esc(top.get("start", "?"))} → {esc(top.get("end", "?"))}</div>'
            f'<p class="signal-note">Peak interval: {esc(pct_s)}{esc(extra)}. '
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
        cards.append(
            f'<div class="signal-card cool">'
            f'<div class="signal-label">Rewrite scan</div>'
            f'<div class="signal-value">No candidate window found</div>'
            f'<p class="signal-note">L1 ran on this article and did not cross the candidate threshold. '
            f'This does not mean the article never changed.</p></div>'
        )
    else:
        cards.append(
            f'<div class="signal-card cool">'
            f'<div class="signal-label">Rewrite</div>'
            f'<div class="signal-value">Analysis not available</div>'
            f'<p class="signal-note">No rewrite timeline was exported for this article. '
            f'This is missing coverage, not a finding that no rewrite occurred.</p></div>'
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

    st = f.stances.get(article)
    if st:
        langs = st.get("langs") or list((st.get("editions") or {}).keys())
        lead_div = _lead_divergence(article, f)
        differs = lead_div is not None and lead_div >= 0.4
        stance_value = "differs by language" if differs else (
            "openings mostly line up" if lead_div is not None else "comparison available"
        )
        cards.append(
            f'<div class="signal-card {"hot" if differs else "cool"}">'
            f'<div class="signal-label">How openings sound</div>'
            f'<div class="signal-value">{stance_value}</div>'
            f'<p class="signal-note">Languages checked: {esc(", ".join(langs))}. '
            f'See <a href="#framing">Framing</a>.</p></div>'
        )

    fr = f.framings.get(article) or {}
    divs = fr.get("divergences") or []
    if divs and not st:
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
        '<h2>Start here</h2>',
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


def missing_diff_section(state="unavailable"):
    if state == "none":
        return (
            '<h2>No candidate rewrite window was found</h2>'
            '<p class="missing-note">The L1 rewrite scan ran, but no interval crossed its candidate '
            'threshold. This is a completed negative result for that detector, not a claim that the '
            'article never changed.</p>'
        )
    return (
        '<h2>Rewrite analysis is not available</h2>'
        '<p class="missing-note">No rewrite timeline was exported for this article. This is a coverage '
        'gap, not evidence that no large or lasting rewrite occurred.</p>'
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


def lexical_section(lex):
    if not lex:
        return ""
    over = _filter_lex_terms(lex.get("overrepresented_after_terms") or lex.get("gained_terms") or [])
    under = _filter_lex_terms(lex.get("underrepresented_after_terms") or lex.get("lost_terms") or [])
    b = lex.get("before") or {}
    a = lex.get("after") or {}
    try:
        jsd = float(lex.get("js_divergence") or 0)
    except (TypeError, ValueError):
        jsd = 0.0
    lab = _lex_label(jsd) or "small vocabulary change"
    span = lex.get("span", "")
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
    return (
        f'<p class="profile">Half of the wording still on the page is about '
        f'<b>{prof["median_age_yrs"]} years</b> old or newer. '
        f'<b>{prof["pct_recent"]}%</b> was written in the last '
        f'{prof["recent_years"]:.0f} years. '
        f'The ten most active accounts wrote <b>{conc}%</b> of what is there now '
        f'({conc_note}; {prof["distinct_editors"]} different accounts overall). '
        f'<span class="muted">This is background only — a small group of writers is common on specialist pages.</span></p>'
    )


def framing_lite_block(fr):
    """Extra cross-language opening comparisons (when present)."""
    if not fr:
        return ""
    divs = fr.get("divergences") or []
    editions = fr.get("editions_compared") or []
    summary = fr.get("summary") or ""
    pivot = fr.get("pivot_window")
    pivot_note = ""
    if pivot:
        pivot_note = (
            f'<p class="muted">Compared around the rewrite from '
            f'{esc(pivot.get("start", "?"))} to {esc(pivot.get("end", "?"))}.</p>'
        )
    head = (
        f'<h3 class="col-h">Where the openings part ways</h3>'
        f'<p class="lead">{WHAT["framing"]}</p>'
    )
    if summary:
        head += f'<p class="brief-sum">{esc(summary)}</p>'
    head += pivot_note
    if editions:
        head += f'<p class="muted">Languages compared: {esc(", ".join(editions))}.</p>'
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
    for d in divs:
        v = d.get("verdict", "differ")
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
        rows += (
            f'<tr><td>{esc(d.get("topic", ""))}</td>'
            f'<td><span class="badge {cls}">{esc(v_plain.get(v, v))}</span></td>'
            f'<td>{en_says}</td><td>{other_says}</td>'
            f'<td class="muted" style="font-size:.82rem">{esc(eds)}</td></tr>'
        )
    table = (
        f'<div class="tablewrap"><table>'
        f'<thead><tr><th scope="col">topic</th><th scope="col">how they compare</th>'
        f'<th scope="col">English says</th><th scope="col">other language(s) say</th>'
        f'<th scope="col">languages</th></tr></thead>'
        f'<tbody>{rows}</tbody></table></div>'
    )
    return (
        head
        + '<p class="legend"><span class="badge v-c">contradict</span> opposite claims · '
        '<span class="badge v-d">differ</span> different emphasis · '
        '<span class="badge v-i">missing</span> only one side mentions it</p>'
        + table
        + '<p class="muted">Differences are invitations to read both sides — not a score of who is right.</p>'
    )


def framing_tab(article, f):
    """Combine L2 stance grid + Framing Lite when either is present."""
    st = f.stances.get(article)
    fr = f.framings.get(article)
    if not st and not fr:
        return ""
    parts = []
    if st:
        parts.append(stance_section(st, f.diver, article))
    if fr:
        parts.append(framing_lite_block(fr))
    return "".join(parts)


def _layer_flags(article, f):
    has_lex = article in f.lexical
    rewrite_state = _rewrite_state(article, f)
    has_src = article in f.sources
    has_framing = bool(f.stances.get(article) or f.framings.get(article))
    has_facts = bool(f.factchecks.get(article))
    has_rev = bool(f.receipts.get(article))
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
    panels = [("Start here", overview_section(article, f, layers), "overview")]
    if pv:
        panels.append(("Rewrite", render_pivots(pv, slugify(article)), "diff"))
    elif diff:
        panels.append(("Rewrite", diff_section(diff), "diff"))
    else:
        panels.append(("Rewrite", missing_diff_section(_rewrite_state(article, f)), "diff"))
    if lex:
        panels.append(("Vocabulary", lexical_section(lex), "lexical"))
    if src:
        panels.append(("Citations", sources_section(article, src), "sources"))
    framing_html = framing_tab(article, f)
    if framing_html:
        panels.append(("Framing", framing_html, "framing"))
    fcs = f.factchecks.get(article)
    if fcs:
        panels.append(("Facts", fact_section(article, fcs), "facts"))
    rec = f.receipts.get(article)
    if rec:
        panels.append(("Versions", receipts_section(rec), "revisions"))
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
    elif (_lead_divergence(article, f) or 0) >= 0.4:
        badges.append('<span class="sig framing">openings differ</span>')

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
            "framing" if f.stances.get(a) or f.framings.get(a) else "",
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


def simple_page(title, body, active, path=None):
    path = path or (f"{active}.html" if active != "about" else "index.html")
    page_title = {
        "about": "About WikiDrift",
        "methodology": "How WikiDrift works",
        "glossary": "Reading tips — WikiDrift",
    }.get(active, f"{title} — WikiDrift")
    desc = {
        "about": "What WikiDrift is: a plain look at how Wikipedia articles change over time.",
        "methodology": "How WikiDrift measures rewrites and language differences — in plain language.",
        "glossary": "Short reading tips for WikiDrift pages.",
    }.get(active, f"{title} — WikiDrift.")
    return render_page(
        title=page_title,
        body=f'<div class="prose">{body}</div>',
        path=path,
        description=desc,
        active=active,
    )


ABOUT_BODY = _md_asset("about")
FINDINGS_BODY = _md_asset("findings")

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
    (SITE / "methodology.html").write_text(
        simple_page("How it works", METHODOLOGY_BODY, "methodology"), encoding="utf-8")
    # Still published at glossary.html so old bookmarks work; page is "Reading tips."
    (SITE / "glossary.html").write_text(
        simple_page("Reading tips", GLOSSARY_BODY, "glossary"), encoding="utf-8")
    for a in articles:
        (SITE / "article" / f"{slugify(a)}.html").write_text(article_page(a, f, categories), encoding="utf-8")
        if a in f.pivots:
            for i, p in enumerate(f.pivots[a]["pivots"]):
                (SITE / "article" / f"{slugify(a)}.p{i}.html").write_text(pivot_page(a, p, i), encoding="utf-8")
    print(f"built {len(articles)} article pages + home(about)/findings/methodology/glossary -> {SITE}")
    print(f"CNAME {CUSTOM_DOMAIN}")
    for a in articles:
        extras = "".join(t for t, has in (("P", a in f.pivots), ("D", a in f.diffs), ("B", a in f.blames),
                                           ("4", a in f.l4)) if has)
        print(f"  - {a}{' [' + extras + ']' if extras else ''}")


if __name__ == "__main__":
    main()
