"""L2.5 lexical drift — term-level change around the L1 pivot (lead, never verdict).

This layer complements L1/L2 with a lightweight, reproducible lexical signal:
- term keyness (smoothed log-odds, after vs before),
- distribution drift (Jensen-Shannon divergence),
computed on article prose snapshots around the L1 pivot.

No model training and no LLM key required.
"""

from __future__ import annotations

import collections
import math
import re

import duckdb

from . import config
from .corpus import Corpus
from .stance import prose_at


_STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "by", "for", "from", "has", "he", "in", "is", "it",
    "its", "of", "on", "that", "the", "to", "was", "were", "will", "with", "this", "these", "those", "or",
    "but", "if", "then", "than", "about", "into", "over", "under", "such", "their", "there", "which",
    "also", "can", "could", "may", "might", "would", "should", "been", "being", "have", "had", "do", "does",
    "did", "not", "no", "yes", "we", "they", "you", "i", "our", "your", "them", "his", "her", "hers",
    "him", "she", "who", "whom", "what", "when", "where", "why", "how", "because", "during", "after",
    "before", "between", "within", "without", "per", "via", "up", "down", "out", "all", "any", "some", "most",
    "more", "less", "many", "few", "each", "other", "same", "new", "old", "one", "two", "three"
}

DEFAULT_MIN_TOKENS = 100
DEFAULT_MAX_SIZE_RATIO = 4.0


def _tokenize(text):
    """Unicode-aware tokenization with alpha-only terms and lowercase normalization."""
    rough = re.findall(r"\b\w+\b", text.lower(), flags=re.UNICODE)
    return [t for t in rough if t.isalpha() and len(t) >= 3]


def _counts(text):
    toks = [t for t in _tokenize(text) if t not in _STOPWORDS]
    return collections.Counter(toks)


def _js_divergence(a_counts, b_counts):
    """Jensen-Shannon divergence on token distributions (base e; bounded by ln(2))."""
    keys = set(a_counts) | set(b_counts)
    if not keys:
        return 0.0
    a_total = sum(a_counts.values())
    b_total = sum(b_counts.values())
    if a_total <= 0 or b_total <= 0:
        return 0.0

    js = 0.0
    for k in keys:
        p = a_counts.get(k, 0) / a_total
        q = b_counts.get(k, 0) / b_total
        m = 0.5 * (p + q)
        if p > 0:
            js += 0.5 * p * math.log(p / m)
        if q > 0:
            js += 0.5 * q * math.log(q / m)
    return round(js, 4)


def _log_odds(before_counts, after_counts, alpha=0.5, min_total=3, top_n=20):
    """Smoothed log-odds ranking: positive = overrepresented after; negative = before."""
    b_total = sum(before_counts.values())
    a_total = sum(after_counts.values())
    if b_total <= 0 or a_total <= 0:
        return [], []

    rows = []
    for tok in (set(before_counts) | set(after_counts)):
        b = before_counts.get(tok, 0)
        a = after_counts.get(tok, 0)
        if b + a < min_total:
            continue
        if a == b:
            continue
        # log((a+alpha)/(A-a+alpha)) - log((b+alpha)/(B-b+alpha))
        a_non = max(0, a_total - a)
        b_non = max(0, b_total - b)
        lo = math.log((a + alpha) / (a_non + alpha)) - math.log((b + alpha) / (b_non + alpha))
        rows.append({
            "term": tok,
            "before": b,
            "after": a,
            "delta": a - b,
            "log_odds": round(lo, 4),
        })

    over_after = sorted([r for r in rows if r["log_odds"] > 0], key=lambda r: (-r["log_odds"], -r["delta"], r["term"]))
    under_after = sorted([r for r in rows if r["log_odds"] < 0], key=lambda r: (r["log_odds"], r["delta"], r["term"]))
    return over_after[:top_n], under_after[:top_n]


def _window_revs(con, article, mode="whole_history", window=None):
    """Select an explicit confirmed pair or whole-history snapshot endpoints."""
    if mode == "pivot_relative":
        if not window or window.get("status") != "confirmed":
            return None
        before_timestamp = window.get("before_timestamp")
        after_timestamp = window.get("after_timestamp")
        return {
            "before_date": before_timestamp[:10] if before_timestamp else None,
            "before_rev": window["before_revid"],
            "after_date": after_timestamp[:10] if after_timestamp else None,
            "after_rev": window["after_revid"],
            "pivot": window.get("start"),
            "interval_source": "exact_confirmation",
            "span": f"rev {window['before_revid']} -> rev {window['after_revid']} (exact confirmation)",
        }
    if mode != "whole_history":
        return None

    snaps = Corpus(con).snapshots(article)
    if len(snaps) < 2:
        return None
    before, after = snaps[0], snaps[-1]
    return {
        "before_date": before[0], "before_rev": before[1],
        "after_date": after[0], "after_rev": after[1],
        "pivot": None,
        "interval_source": "snapshot_endpoints",
        "span": f"{before[0]} -> {after[0]} (whole history)",
    }


def _baseline_adequacy(before_tokens, after_tokens, min_tokens, max_size_ratio):
    """Return whether token baselines are large and balanced enough to compare."""
    smaller = min(before_tokens, after_tokens)
    larger = max(before_tokens, after_tokens)
    size_ratio = round(larger / smaller, 4) if smaller else float("inf")
    if smaller < min_tokens:
        return False, f"minimum token floor is {min_tokens}; observed {smaller}", size_ratio
    if size_ratio > max_size_ratio:
        return False, f"before/after size ratio {size_ratio} exceeds {max_size_ratio}", size_ratio
    return True, None, size_ratio


def lexical_drift(
    article,
    top_n=20,
    min_total=3,
    persist=True,
    mode="whole_history",
    window=None,
    min_tokens=DEFAULT_MIN_TOKENS,
    max_size_ratio=DEFAULT_MAX_SIZE_RATIO,
):
    """Compute and persist lexical context for an explicit analysis mode."""
    if mode == "not_applicable":
        out = {
            "article": article,
            "mode": "not_applicable",
            "adequate": False,
            "adequacy_reason": "fresh confirmed revision pair is required",
            "interval_source": None,
        }
        if persist:
            config.write_findings(f"{config.slugify(article)}.lexical.json", out)
        return out

    con = duckdb.connect(str(config.DB), read_only=True)
    win = _window_revs(con, article, mode=mode, window=window)
    con.close()
    if not win:
        print(f"=== L2.5 lexical drift — {article} ===")
        print("  not enough snapshots")
        out = {
            "article": article,
            "mode": "insufficient_baseline",
            "requested_mode": mode,
            "adequate": False,
            "adequacy_reason": "too few snapshots or no confirmed revision pair",
            "interval_source": None,
        }
        if persist:
            config.write_findings(f"{config.slugify(article)}.lexical.json", out)
        return out

    before_text = prose_at(win["before_rev"])
    after_text = prose_at(win["after_rev"])
    b_counts = _counts(before_text)
    a_counts = _counts(after_text)
    before_tokens = int(sum(b_counts.values()))
    after_tokens = int(sum(a_counts.values()))
    adequate, adequacy_reason, size_ratio = _baseline_adequacy(
        before_tokens, after_tokens, min_tokens, max_size_ratio
    )
    jsd = _js_divergence(b_counts, a_counts)
    over_after, under_after = _log_odds(b_counts, a_counts, min_total=min_total, top_n=top_n)

    out = {
        "article": article,
        "mode": mode if adequate else "insufficient_baseline",
        "requested_mode": mode,
        "adequate": adequate,
        "adequacy_reason": adequacy_reason,
        "size_ratio": size_ratio,
        "interval_source": win["interval_source"],
        "span": win["span"],
        "pivot": win["pivot"],
        "before": {"date": win["before_date"], "rev": win["before_rev"], "tokens": before_tokens},
        "after": {"date": win["after_date"], "rev": win["after_rev"], "tokens": after_tokens},
        "js_divergence": jsd,
        "overrepresented_after_terms": over_after,
        "underrepresented_after_terms": under_after,
        "note": "Lexical drift is a lead-strengthening signal (term distribution/context change), not a verdict.",
    }

    print(f"=== L2.5 lexical drift — {article} ===")
    print(f"  {win['span']}")
    if not adequate:
        print(f"  insufficient baseline: {adequacy_reason}")
    print(f"  JS divergence: {jsd}")
    if over_after:
        print("  top terms overrepresented after (relative to before):")
        for r in over_after[:10]:
            print(f"    {r['term']:<20} lo={r['log_odds']:>7}  {r['before']} -> {r['after']}")
    if under_after:
        print("  top terms underrepresented after (relative to before):")
        for r in under_after[:10]:
            print(f"    {r['term']:<20} lo={r['log_odds']:>7}  {r['before']} -> {r['after']}")

    if persist:
        slug = config.slugify(article)
        config.write_findings(f"{slug}.lexical.json", out)
        print(f"  wrote findings/{slug}.lexical.json")
    return out
