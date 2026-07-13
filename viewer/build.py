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

# Plain-language "what am I looking at?" leads per tab (links resolve from an article page: root "../").
WHAT = {
    "diff": 'Side-by-side <a href="../glossary.html#pivot">before / after</a> of the English article around '
            'its heaviest rewrite windows.',
    "blame": 'Who introduced the current opening: each color is one account '
             '(<a href="../glossary.html#blame">blame</a>, VCS-style).',
    "sources": 'How the article\'s own citations <a href="../glossary.html#source-change">changed from '
               '&rarr; to</a> across the rewrite — domains added or dropped. Composition only; '
               '<b>no source is rated</b>.',
    "lexical": 'How vocabulary usage shifted across the rewrite window (distribution drift + top terms '
               'over/under-represented after). Signal only, not a conclusion.',
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
            if isinstance(x, str):
                return x
            if isinstance(x, dict):
                return x.get("article") or x.get("title") or x.get("candidate")
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
        link = f'<a href="{oldid(lang, e["revid"])}" target="_blank" rel="noopener">rev {esc(e["revid"])}</a>'
        rows.append(f"<tr><td><b>{esc(lang)}</b></td><td>{esc(e['title'])}</td>"
                    f"<td>{link}</td><td>{esc(e.get('timestamp',''))}</td>"
                    f"<td>{e.get('prose_chars',0):,}</td></tr>")
    qid = esc(rec.get("qid", ""))
    return ('<p class="lead">The exact Wikipedia revisions each finding was computed from, so anyone can '
            'verify. Wikidata <a href="https://www.wikidata.org/wiki/' + qid + '" target="_blank" '
            f'rel="noopener">{qid}</a>.</p>'
            '<div class="tablewrap"><table><thead><tr><th scope="col">edition</th><th scope="col">title</th>'
            '<th scope="col">revision</th><th scope="col">timestamp</th><th scope="col">prose</th></tr></thead>'
            f'<tbody>{"".join(rows)}</tbody></table></div>')


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
            npov = "!" if r.get("npov_departure") else ""
            quote = (r.get("evidence") or "").strip()
            eid += 1
            cid = f"ev{eid}"
            if quote:
                cells.append(
                    f'<td class="sc-{SCLASS.get(s,"a")}" style="padding:0">'
                    f'<button type="button" class="cell-ev sc-{SCLASS.get(s,"a")}" '
                    f'aria-expanded="false" aria-controls="{cid}">{esc(s)}{npov}</button></td>')
                ev_rows.append(
                    f'<tr class="ev-row" id="{cid}" hidden><td colspan="{len(langs)+1}" class="ev-panel">'
                    f'<b>{esc(l)} · {esc(e)}</b> — {esc(quote)}</td></tr>')
            else:
                cells.append(f'<td class="cell sc-{SCLASS.get(s,"a")}">{esc(s)}{npov}</td>')
        rows.append(f'<tr><th scope="row">{esc(e)}</th>{"".join(cells)}</tr>')
        rows.extend(ev_rows)
    legend = ('<span class="chip sc-c">critical</span><span class="chip sc-n">neutral</span>'
              '<span class="chip sc-s">sympathetic</span><span class="chip sc-a">absent</span>')
    return (f'<div class="tablewrap"><table class="grid"><thead><tr><th></th>{head}</tr></thead>'
            f'<tbody>{"".join(rows)}</tbody></table></div>'
            f'<p class="legend">{legend} <span class="muted">! = departs from neutral · click a cell for the evidence quote</span></p>')


def framing_section(article, st, diver):
    parts = ['<h2>How the editions frame it</h2>', f'<p class="lead">{WHAT["framing"]}</p>']
    stat = diver.get("static", {}).get(article)
    if stat:
        d = stat["variants"]["lead"]["divergence"]
        word = "agree closely" if d < 0.4 else ("differ moderately" if d < 1.2 else "differ sharply")
        parts.append(f'<p>Across editions, the framing <b>{word}</b> '
                     f'<span class="muted">(divergence {d:.2f} on a 0–2 scale).</span></p>')
    if st:
        parts.append(stance_grid(st))
    pr = diver.get("pivot_relative", {}).get(article)
    if pr:
        if pr["read"] == "PEELED AWAY":
            msg = ("Before the major rewrite the editions largely agreed on framing; afterward, the English "
                   "edition moved away from them. That divergence-at-a-rewrite is the strongest kind of lead.")
        elif pr["read"] == "no net change":
            msg = ("The editions differ, but roughly the same amount before and after the rewrite, so the "
                   "difference looks inborn rather than introduced at a single moment.")
        else:
            msg = "The editions moved closer together across the rewrite."
        parts.append(f'<div class="callout"><b>Across the rewrite.</b> {msg}</div>')
    return "".join(parts)


def fact_section(article, fcs):
    if not fcs:
        return ""
    times = sorted(fcs, key=lambda t: (t == "now", t))          # dated (chronological) first, 'now' last
    order = [q["question"] for q in fcs[times[0]]["claim"]["adjudication"]]
    verdict_by = {t: {q["question"]: q for q in fcs[t]["claim"]["adjudication"]} for t in times}
    sev = {"contradict": 3, "differ": 2, "insufficient": 1, "agree": 0}
    thead = "".join(f'<th scope="col">{esc(t)}</th>' for t in times)
    rows = []
    for q in order:
        cells = []
        for t in times:
            a = verdict_by[t].get(q)
            if a:
                v = a["verdict"]
                if v == "agree":
                    cells.append(f'<td class="muted" style="font-size:.82rem">agree</td>')
                else:
                    cells.append(f'<td><span class="badge v-{VCLASS.get(v,"i")}">{esc(v)}</span></td>')
            else:
                cells.append("<td>—</td>")
        worst = max((verdict_by[t][q] for t in times if q in verdict_by[t]),
                    key=lambda a: sev.get(a["verdict"], 0), default=None)
        note = worst.get("note", "") if worst else ""
        rows.append(f'<tr><th scope="row">{esc(q)}</th>{"".join(cells)}</tr>'
                    f'<tr class="noterow"><td colspan="{len(times)+1}" class="muted">{esc(note)}</td></tr>')
    cite = " · ".join(f"{esc(t)}: {fcs[t]['citation']['mean_jaccard']}" for t in times)
    return (f'<h2>Factual cross-edition check</h2><p class="lead">{WHAT["facts"]}</p>'
            f'<div class="tablewrap"><table class="grid"><thead><tr><th scope="col">factual question</th>{thead}</tr>'
            f'</thead><tbody>{"".join(rows)}</tbody></table></div>'
            f'<p class="muted">Shared-source overlap (context only, skewed by edition language): {cite}.</p>')


def mscore_section(article, mscore):
    m = mscore.get(article)
    if not m:
        return ""
    rpr = m.get("refined_per_rev", 0)
    contested = rpr >= 5
    cls = "warn" if contested else "ok"
    read = ("High mutual-revert activity (edit-wars)."
            if contested else "Low mutual-revert activity — changes were not heavily fought.")
    return (f'<h2>Edit-war intensity</h2>'
            f'<p><span class="pill {cls}">{("high" if contested else "low")}</span> '
            f'<span class="muted">{m["refined"]["M"]:,} conflict weight over {m["raw"]["revs"]:,} revisions.</span></p>'
            f'<p class="muted">{esc(read)} Context only — contested ≠ biased.</p>')


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
        chips = " ".join(f'<span class="chip" style="background:{c}">{esc(n)}</span>' for n, c in list(colors.items())[:12])
        legend = f'<p class="legend">Added text colored by editor (best match): {chips}</p>'
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
            rows.append('<div class="drow"><div class="dfull muted">This was a near-total rewrite — most of the '
                        'article changed, so only the first several hundred changes are shown here. Open either '
                        'full revision from the <b>Revisions</b> tab to read everything.</div></div>')
            continue
        rem, add = c.get("rem", ""), c.get("add", "")
        left = f'<div class="dl del"><span class="mk">−</span>{esc(rem)}</div>' if rem else '<div class="dl empty"></div>'
        right = f'<div class="dr add"><span class="mk">+</span>{esc(add)}</div>' if add else '<div class="dr empty"></div>'
        rows.append(f'<div class="drow">{left}{right}</div>')
    if len(rows) == 1:
        rows.append('<div class="drow"><div class="dfull muted">No prose-level changes at this point.</div></div>')
    return f'<div class="authdiff" role="table" aria-label="before and after, side by side">{"".join(rows)}</div>'


def diff_section(diff):
    return (f'<h2>What changed, and when</h2><p class="lead">{WHAT["diff"]}</p>'
            f'<p class="muted"><del>struck red</del> = removed · <ins>highlighted</ins> = added · '
            f'{esc(diff["before"]["date"])} vs now.</p>' + redline(diff["before"]["text"], diff["after"]["text"]))


def render_pivots(pv, slug):
    items = "".join(
        f'<a class="pv-link" href="{slug}.p{i}.html"><span><b>{esc(p["start"])} → {esc(p["end"])}</b>'
        f'<span class="muted"> · {p["peak_pct"]:.0f}% rewritten</span></span>'
        f'<span class="f-go" aria-hidden="true">→</span></a>' for i, p in enumerate(pv["pivots"]))
    return (f'<h2>What changed, and when</h2><p class="lead">{WHAT["diff"]}</p>'
            f'<p class="muted">The article was heavily rewritten at these '
            f'<a href="../glossary.html#pivot">pivots</a>. Open one to read the before → after as tracked changes.</p>'
            f'<div class="pvlinks">{items}</div>')


def pivot_page(article, p, i):
    slug = slugify(article)
    body = (
        f'<div class="page-intro"><p class="kicker"><a href="{slug}.html">← {esc(article)}</a></p>'
        f'<h1>Rewrite · {esc(p["start"])} → {esc(p["end"])}</h1>'
        f'<p class="summary">About {p["peak_pct"]:.0f}% of the article changed in this window. Below is the '
        f'before → after as tracked changes: <del>struck red</del> = removed, <ins>highlighted</ins> = added '
        f'(colored by who added it).</p>'
        f'<p class="disclaimer">Candidate only — not a conclusion.</p></div>'
        f'<div class="workspace">'
        + redline(p["before_text"], p["after_text"], p.get("authors_after"), p.get("authors_before"))
        + '</div>')
    return render_page(
        title=f"{article} rewrite {p['start']} — WikiDrift",
        body=body, root="../", path=f"article/{slug}.p{i}.html",
        description=f"Before/after redline for {article} ({p['start']} → {p['end']}).",
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
def _fact_improved(fcs):
    if "now" not in fcs or len(fcs) < 2:
        return False
    now = {q["question"]: q["verdict"] for q in fcs["now"]["claim"]["adjudication"]}
    for t, d in fcs.items():
        if t == "now":
            continue
        for q in d["claim"]["adjudication"]:
            if q["verdict"] in ("differ", "contradict") and now.get(q["question"]) == "agree":
                return True
    return False


def headline(article, f):
    has_pivot = article in f.pivots or article in f.diffs
    lex = f.lexical.get(article) or {}
    jsd = float(lex.get("js_divergence", 0) or 0)
    if has_pivot and jsd > 0.1:
        return "A major rewrite was detected, with a measurable shift in vocabulary."
    if has_pivot:
        return "A major rewrite was detected in this article's edit history."
    if jsd > 0.1:
        return "Vocabulary shifted significantly around the rewrite window."
    return "Findings compiled."


# ---- pages ------------------------------------------------------------------
PAGE = _asset("templates/page.html")

NAV_KEYS = ("findings", "about", "methodology", "glossary")


def render_page(*, title, body, root="", path="index.html", description=None, active=None):
    """Fill the family page shell (meta, nav, footer)."""
    desc = description or (
        "WikiDrift measures Wikipedia article change and cross-edition disagreement from public data. "
        "A diagnostic tool from encyclopediae.org.")
    canon = f"{SITE_ORIGIN}/{path.lstrip('/')}" if path != "index.html" else f"{SITE_ORIGIN}/"
    nav = {k: ' class="active" aria-current="page"' if k == active else "" for k in NAV_KEYS}
    return PAGE.format(
        title=title, description=esc(desc), canonical=canon, root=root, body=body,
        nav_findings=nav["findings"], nav_about=nav["about"],
        nav_methodology=nav["methodology"], nav_glossary=nav["glossary"],
    )


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


def overview_section(article, f, lead, layers):
    """Plain first tab: summary, layers present/missing, Wikipedia link, optional L4 note."""
    parts = [
        f'<h2>Overview</h2>',
        f'<p class="lead">{esc(lead)}</p>',
        f'<a class="wiki-link" href="{esc(wiki_en_url(article))}" target="_blank" rel="noopener">'
        f'Open current English Wikipedia article ↗</a>',
    ]

    # Pivot signal — shown before M-score so severity is immediately clear
    pwr = pivot_score(article, f)
    if article in f.pivots or article in f.diffs:
        tier = "high" if pwr >= 200_000 else ("med" if pwr >= 50_000 else "")
        pill_cls = {"high": "warn", "med": "warn", "": ""}.get(tier, "")
        badge_cls = f"sig {tier}".strip()
        pv = f.pivots.get(article) or {}
        pivs = pv.get("pivots") or []
        n = len(pivs)
        label = f"pivot {_fmt_pwr(pwr)}" if pwr else "pivot detected"
        pivot_note = f"{n} confirmed pivot{'s' if n != 1 else ''}" if n else "diff detected"
        parts.append(
            f'<p style="margin:.65rem 0 .35rem"><span class="{badge_cls}">{label}</span> '
            f'<span class="muted" style="font-size:.88rem">{pivot_note} — see Diff tab</span></p>'
        )
    chips = []
    for name, have, note in layers:
        cls = "have" if have else "miss"
        label = name if have else f"{name} — {note}"
        chips.append(f'<li class="{cls}">{esc(label)}</li>')
    parts.append('<p class="muted" style="margin-bottom:.35rem">Layers on this page</p>'
                 f'<ul class="layer-list">{"".join(chips)}</ul>')
    l4 = f.l4.get(article)
    if l4:
        seed = esc(l4.get("seed") or "a seed article")
        parts.append(
            f'<div class="callout"><b>Discovery path.</b> Surfaced via L4 graph-guided discovery seeded from '
            f'{seed} (search prior only — this article was re-tested on its own content). '
            f'Class: {esc(l4.get("class") or "lead")}.</div>')
    ms = mscore_section(article, f.mscore)
    if ms:
        parts.append(ms)
    parts.append(profile_line(f.profiles.get(article)))
    parts.append(
        '<p class="muted">Use the tabs for lexical drift, diffs, and citation change. '
        'Deep-link with <code>#lexical</code>, <code>#diff</code>, <code>#sources</code>.</p>')
    return "".join(p for p in parts if p)


def missing_diff_section():
    return (
        '<h2>What changed, and when</h2>'
        '<p class="missing-note">No confirmed pivot was detected for this article — '
        'L1 did not find a durable rewrite large enough to redline. '
        'Other layers (lexical, sources) may still apply.</p>'
    )


def sources_section(article, src):
    if not src:
        return ""
    b, a = src["before"], src["after"]

    def mix(m):
        return ", ".join(f"{esc(k)} {v}%" for k, v in m.items()) or "—"

    def rows(items):
        out = "".join(f'<tr><td>{esc(x["domain"])}</td><td class="fromto">{x["from"]} → {x["to"]}</td></tr>'
                      for x in items[:12])
        return out or '<tr><td colspan="2" class="muted">none</td></tr>'
    return (
        '<h2>What the sourcing changed</h2>'
        f'<p class="lead">{WHAT["sources"]}</p>'
        f'<p>{esc(src["span"])}. References {b["refs"]} → {a["refs"]}; distinct domains '
        f'{b["n_domains"]} → {a["n_domains"]}.</p>'
        f'<p>Citation-type mix: <b>{mix(b["cite_mix"])}</b> &nbsp;→&nbsp; <b>{mix(a["cite_mix"])}</b>.</p>'
        '<div class="srcgrid">'
        '<div class="tablewrap"><table><thead><tr><th scope="col">source added / grown</th>'
        f'<th scope="col">from → to</th></tr></thead><tbody>{rows(src["added"])}</tbody></table></div>'
        '<div class="tablewrap"><table><thead><tr><th scope="col">source dropped / reduced</th>'
        f'<th scope="col">from → to</th></tr></thead><tbody>{rows(src["dropped"])}</tbody></table></div>'
        '</div>'
        '<p class="muted">Domains counted from citation markup (archive links unwrapped to the original '
        'source).</p>')


def lexical_section(lex):
    if not lex:
        return ""
    over = lex.get("overrepresented_after_terms") or lex.get("gained_terms") or []
    under = lex.get("underrepresented_after_terms") or lex.get("lost_terms") or []

    def rows(items):
        out = "".join(
            f'<tr><td>{esc(x.get("term", ""))}</td><td>{x.get("before", 0)}</td><td>{x.get("after", 0)}</td>'
            f'<td>{x.get("delta", 0)}</td><td>{x.get("log_odds", 0)}</td></tr>'
            for x in items[:12])
        return out or '<tr><td colspan="5" class="muted">none</td></tr>'

    b = lex.get("before") or {}
    a = lex.get("after") or {}
    jsd = lex.get("js_divergence", "n/a")
    span = lex.get("span", "")
    return (
        '<h2>How vocabulary shifted</h2>'
        f'<p class="lead">{WHAT["lexical"]}</p>'
        f'<p>{esc(span)}. Token counts {b.get("tokens", 0)} → {a.get("tokens", 0)}. '
        f'Jensen-Shannon divergence <b>{esc(jsd)}</b>.</p>'
        '<div class="srcgrid">'
        '<div class="tablewrap"><table><thead><tr><th scope="col">overrepresented after</th>'
        '<th scope="col">before</th><th scope="col">after</th><th scope="col">delta</th>'
        '<th scope="col">log-odds</th></tr></thead>'
        f'<tbody>{rows(over)}</tbody></table></div>'
        '<div class="tablewrap"><table><thead><tr><th scope="col">underrepresented after</th>'
        '<th scope="col">before</th><th scope="col">after</th><th scope="col">delta</th>'
        '<th scope="col">log-odds</th></tr></thead>'
        f'<tbody>{rows(under)}</tbody></table></div>'
        '</div>'
        '<p class="muted">Relative term keyness from smoothed log-odds. Interpretation is directional only.</p>')


def profile_line(prof):
    """A single-line, aggregate drift-profile strip (recency + editor concentration). No editor names."""
    if not prof or prof.get("reason"):
        return ""
    return ('<p class="profile">Current text: median age '
            f'<b>{prof["median_age_yrs"]} yr</b> · <b>{prof["pct_recent"]}%</b> authored in the last '
            f'{prof["recent_years"]:.0f} years · top-10 <a href="../glossary.html#concentration">editors</a> '
            f'wrote <b>{prof["top10_editor_share"]}%</b> of it ({prof["distinct_editors"]} distinct editors). '
            '<span class="muted">Descriptive context only.</span></p>')


def framing_section(fr):
    """Render the Framing Lite tab: cross-lingual lead divergences."""
    if not fr:
        return ""
    divs = fr.get("divergences") or []
    editions = fr.get("editions_compared") or []
    summary = fr.get("summary") or ""
    category = fr.get("category") or "general"
    pivot = fr.get("pivot_window")

    pivot_note = ""
    if pivot:
        pivot_note = (f'<p class="muted">Pivot window: {esc(pivot.get("start","?"))} – '
                      f'{esc(pivot.get("end","?"))}</p>')

    if not divs:
        return (f'<h2>Cross-lingual framing</h2>'
                f'<p class="lead">Compared: {", ".join(editions)}. '
                f'No significant divergences found.</p>{pivot_note}')

    verdict_cls = {"contradict": "v-c", "differ": "v-d", "absent_en": "v-d",
                   "absent_other": "v-i", "agree": "v-a"}
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
        rows += (f'<tr><td>{esc(d.get("topic",""))}</td>'
                 f'<td class="cell {cls}">{esc(v)}</td>'
                 f'<td>{en_says}</td><td>{other_says}</td>'
                 f'<td class="muted" style="font-size:.82rem">{esc(eds)}</td></tr>')

    table = (f'<div class="tablewrap"><table>'
             f'<thead><tr><th>Topic</th><th>Verdict</th><th>EN says</th>'
             f'<th>Other edition(s) say</th><th>Editions</th></tr></thead>'
             f'<tbody>{rows}</tbody></table></div>')

    return (f'<h2>Cross-lingual framing</h2>'
            f'<p class="lead">{esc(summary)}</p>'
            f'{pivot_note}'
            f'<p class="muted">Editions compared: {", ".join(editions)} · category: {esc(category)}</p>'
            f'<p class="legend"><span class="badge v-c">contradict</span> factual contradiction &nbsp;'
            f'<span class="badge v-d">differ</span> emphasis differs &nbsp;'
            f'<span class="badge v-i">absent</span> missing from one edition</p>'
            f'{table}'
            f'<p class="disclaimer">Divergences are leads for researchers, not verdicts.</p>')


def _layer_flags(article, f):
    has_lex = article in f.lexical
    has_diff = article in f.pivots or article in f.diffs
    has_src = article in f.sources
    has_framing = bool((f.framings.get(article) or {}).get("divergences"))
    return [
        ("Lexical", has_lex, "not run"),
        ("Diff", has_diff, "no pivot found"),
        ("Sources", has_src, "not run"),
        ("Framing", has_framing, "not run"),
    ]


def article_page(article, f, categories):
    diff, pv = f.diffs.get(article), f.pivots.get(article)
    src = f.sources.get(article)
    lex = f.lexical.get(article)
    fr = f.framings.get(article)
    lead = headline(article, f)
    layers = _layer_flags(article, f)
    panels = [("Overview", overview_section(article, f, lead, layers), "overview")]
    if lex:
        panels.append(("Lexical", lexical_section(lex), "lexical"))
    if pv:
        panels.append(("Diff", render_pivots(pv, slugify(article)), "diff"))
    elif diff:
        panels.append(("Diff", diff_section(diff), "diff"))
    else:
        panels.append(("Diff", missing_diff_section(), "diff"))
    if src:
        panels.append(("Sources", sources_section(article, src), "sources"))
    if fr:
        panels.append(("Framing", framing_section(fr), "framing"))
    cat = _category_for(article, categories)
    body = (
        f'<div class="page-intro"><p class="kicker">{esc(cat)}</p><h1>{esc(article)}</h1>'
        f'<p class="summary">{esc(lead)}</p>'
        '<p class="disclaimer">Candidate only — not a conclusion.</p></div>'
        f'<div class="workspace">{tabs(panels)}</div>')
    return render_page(
        title=f"{article} — WikiDrift",
        body=body, root="../", path=f"article/{slugify(article)}.html",
        description=lead, active="findings",
    )


INDEX_JS = _asset("index.js")


def _fmt_pwr(n):
    """Compact PWR-mass label: 827,154 → '827k'."""
    return f"{n // 1000}k" if n >= 1000 else str(n)


def _stance_departed(article, f):
    """True if L2 found npov_departure on any entity in any edition."""
    eds = (f.stances.get(article) or {}).get("editions") or {}
    for ed in eds.values():
        for slot in ed.values():
            if isinstance(slot, dict):
                for ent in slot.values():
                    if isinstance(ent, dict) and ent.get("npov_departure"):
                        return True
    return False


def signal_badges(article, f, score):
    """Severity cues for the index list."""
    badges = []
    pwr = pivot_score(article, f)
    if article in f.pivots or article in f.diffs:
        label = f"pivot {_fmt_pwr(pwr)}" if pwr else "diff"
        tier = "high" if pwr >= 200_000 else ("med" if pwr >= 50_000 else "")
        cls = f"sig {tier}".strip()
        badges.append(f'<span class="{cls}">{label}</span>')
    if article in f.lexical:
        js = lexical_score(article, f)
        badges.append(f'<span class="sig lex">lex {js:.2f}</span>')
    fr = f.framings.get(article) or {}
    if fr.get("divergences"):
        has_contradict = any(d.get("verdict") == "contradict" for d in fr["divergences"])
        badge_cls = "sig framing-c" if has_contradict else "sig framing"
        badges.append(f'<span class="{badge_cls}">framing</span>')
    prof = f.profiles.get(article) or {}
    conc = prof.get("top10_editor_share") or 0
    if conc >= 70:
        badges.append(f'<span class="sig conc">{conc:.0f}% conc</span>')
    if article in f.l4:
        badges.append('<span class="sig">L4</span>')
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


def index_page(articles, f, categories):
    cats = sorted({_category_for(a, categories) for a in articles})
    chips = ('<button type="button" class="fchip active" data-cat="all" aria-pressed="true">All</button>'
             + "".join(f'<button type="button" class="fchip" data-cat="{esc(c)}" aria-pressed="false">{esc(c)}</button>'
                       for c in cats))
    rows = []
    for a in articles:
        h = headline(a, f)
        cat = _category_for(a, categories)
        lex_sc = lexical_score(a, f)
        pwr_sc = pivot_score(a, f)
        badges = signal_badges(a, f, None)
        meta = f'<div class="f-meta">{badges}</div>' if badges else ""
        meta_html = meta if meta else '<div class="f-meta"></div>'
        lex_text = f" lexical {lex_sc:.3f}" if a in f.lexical else ""
        # grid: title | summary | badges | arrow  (CSS rearranges on narrow screens)
        rows.append(
            f'<a class="finding" href="article/{slugify(a)}.html" data-cat="{esc(cat)}" '
            f'data-title="{esc(a.lower())}" data-score="{lex_sc}" '
            f'data-lex="{lex_sc}" data-pwr="{pwr_sc}" '
            f'data-text="{esc((a + " " + h + " " + cat + lex_text).lower())}">'
            f'<div class="f-head"><span class="kicker">{esc(cat)}</span>'
            f'<h3>{esc(a)}</h3></div>'
            f'<div class="f-body"><p>{esc(h)}</p>{meta_html}</div>'
            f'<span class="f-go" aria-hidden="true">→</span></a>')
    intro = (
        '<div class="page-intro"><h1>WikiDrift findings</h1>'
        '<p class="summary">Each article against its own history: '
        'major rewrites, vocabulary shift, and citation changes. '
        'A diagnostic tool from <a href="https://encyclopediae.org">encyclopediae.org</a>.</p>'
        '<p class="disclaimer">Candidates only — not conclusions.</p></div>')
    controls = (
        '<div class="controls"><input id="q" class="search" type="search" '
        'placeholder="Search findings…" aria-label="Search findings">'
        f'<div class="filters" role="group" aria-label="Filter by topic">{chips}</div>'
        '<label class="sortlab">Sort <select id="sort" class="sortsel" aria-label="Sort findings">'
        '<option value="lex" selected>Lexical drift</option>'
        '<option value="pwr">Pivot (PWR mass)</option>'
        '<option value="az">A–Z</option>'
        '<option value="cat">Topic</option></select></label>'
        '<span id="count" class="count" role="status" aria-live="polite"></span></div>')
    body = (intro + '<div class="workspace">' + controls
            + f'<div class="findings">{"".join(rows)}</div>'
            + '<p id="empty" hidden>No findings match this filter.</p>'
            + '<nav class="pager" id="pager" aria-label="Findings pages"></nav></div>' + INDEX_JS)
    return render_page(
        title="WikiDrift findings",
        body=body, path="index.html",
        description="Findings from WikiDrift: Wikipedia change and cross-edition disagreement, from public data.",
        active="findings",
    )


def simple_page(title, body, active):
    return render_page(
        title=f"{title} — WikiDrift",
        body=f'<div class="prose">{body}</div>',
        path=f"{active}.html" if active != "findings" else "index.html",
        description=f"{title} — WikiDrift, a diagnostic tool from encyclopediae.org.",
        active=active,
    )


ABOUT_BODY = _md_asset("about")

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
    (SITE / "index.html").write_text(index_page(articles, f, categories), encoding="utf-8")
    (SITE / "about.html").write_text(simple_page("About", ABOUT_BODY, "about"), encoding="utf-8")
    (SITE / "methodology.html").write_text(simple_page("Methodology", METHODOLOGY_BODY, "methodology"), encoding="utf-8")
    (SITE / "glossary.html").write_text(simple_page("Glossary", GLOSSARY_BODY, "glossary"), encoding="utf-8")
    for a in articles:
        (SITE / "article" / f"{slugify(a)}.html").write_text(article_page(a, f, categories), encoding="utf-8")
        if a in f.pivots:
            for i, p in enumerate(f.pivots[a]["pivots"]):
                (SITE / "article" / f"{slugify(a)}.p{i}.html").write_text(pivot_page(a, p, i), encoding="utf-8")
    print(f"built {len(articles)} article pages + index/about/methodology/glossary -> {SITE}")
    print(f"CNAME {CUSTOM_DOMAIN}")
    for a in articles:
        extras = "".join(t for t, has in (("P", a in f.pivots), ("D", a in f.diffs), ("B", a in f.blames),
                                           ("4", a in f.l4)) if has)
        print(f"  - {a}{' [' + extras + ']' if extras else ''}")


if __name__ == "__main__":
    main()
