#!/usr/bin/env python3
"""OKLCH → sRGB → WCAG 2.1 contrast checker for the viewer's design tokens.

Converts each OKLCH token (Ottosson OKLab matrix) to linear sRGB, gamma-encodes
to 8-bit sRGB exactly as a browser renders it, then re-linearizes and applies the
WCAG 2.1 relative-luminance / contrast-ratio formula. No dependencies — this is
the "real checker": the same math a browser + a WCAG tool would agree on.

Run: python3 viewer/check_contrast.py
Exit code 1 if any AA gate fails.
"""
from __future__ import annotations

import math
import re
import sys

# --- Tokens as authored in site/style.css :root ---------------------------------
# name -> (L%, C, H)
TOKENS = {
    "paper": (99, 0, 0), "surface": (99.6, 0, 0), "chrome": (96.5, 0, 0),
    "ink": (21, 0, 0), "muted": (43, 0, 0), "faint": (52, 0, 0),
    "line": (88, 0, 0), "line-strong": (80, 0, 0), "control": (63, 0, 0),
    "accent": (30, 0, 0), "accent-ink": (26, 0, 0), "accent-wash": (94, 0, 0), "on-dark": (74, 0, 0),
    "crit-bg": (93, 0.055, 25), "crit-fg": (43, 0.16, 25),
    "symp-bg": (93, 0.045, 255), "symp-fg": (43, 0.13, 255),
    "neut-bg": (94.5, 0.006, 260), "neut-fg": (42, 0.02, 260),
    "abs-bg": (97, 0.003, 260), "abs-fg": (53, 0.012, 260),
    "ok-bg": (93, 0.06, 150), "ok-fg": (42, 0.12, 150),
    "warn-bg": (94, 0.07, 80), "warn-fg": (46, 0.11, 70),
}


def oklch_to_srgb8(Lpct: float, C: float, H_deg: float) -> tuple[int, int, int]:
    """OKLCH -> 8-bit sRGB (rounded, clamped), as a browser would rasterize it."""
    L = Lpct / 100.0
    h = math.radians(H_deg)
    a, b = C * math.cos(h), C * math.sin(h)

    l_ = L + 0.3963377774 * a + 0.2158037573 * b
    m_ = L - 0.1055613458 * a - 0.0638541728 * b
    s_ = L - 0.0894841775 * a - 1.2914855480 * b
    l, m, s = l_**3, m_**3, s_**3

    r = 4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s
    g = -1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s
    bl = -0.0041960863 * l - 0.7034186147 * m + 1.7076147010 * s

    def gamma(c: float) -> int:
        c = max(0.0, min(1.0, c))
        c = 12.92 * c if c <= 0.0031308 else 1.055 * (c ** (1 / 2.4)) - 0.055
        return round(max(0.0, min(1.0, c)) * 255)

    return gamma(r), gamma(g), gamma(bl)


def rel_luminance(rgb8: tuple[int, int, int]) -> float:
    """WCAG 2.1 relative luminance from 8-bit sRGB."""
    def lin(v: int) -> float:
        s = v / 255.0
        return s / 12.92 if s <= 0.03928 else ((s + 0.055) / 1.055) ** 2.4
    r, g, b = (lin(c) for c in rgb8)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(fg: str, bg: str) -> float:
    l1 = rel_luminance(oklch_to_srgb8(*TOKENS[fg]))
    l2 = rel_luminance(oklch_to_srgb8(*TOKENS[bg]))
    hi, lo = max(l1, l2), min(l1, l2)
    return (hi + 0.05) / (lo + 0.05)


# --- Pairs the CSS actually renders. kind: 'text'(4.5), 'large'(3.0), 'ui'(3.0) --
PAIRS = [
    # black masthead / footer (light on ink)
    ("paper", "ink", "text", "wordmark on black masthead"),
    ("on-dark", "ink", "text", "nav links / footer text on black"),
    # body + chrome text
    ("ink", "paper", "text", "body text"),
    ("ink", "surface", "text", "text on cards/tables"),
    ("muted", "paper", "text", "summary/muted body"),
    ("muted", "surface", "text", "muted on cards"),
    ("muted", "chrome", "text", "thead"),
    ("faint", "paper", "text", "kicker/count/.pv span (small)"),
    ("faint", "surface", "text", "faint on cards"),
    ("accent-ink", "paper", "text", "links"),
    ("accent-ink", "surface", "text", "links on cards / summary toggle"),
    # score cells + badges (small bold text, ~12-13px -> normal threshold)
    ("crit-fg", "crit-bg", "text", "critical cell/badge"),
    ("symp-fg", "symp-bg", "text", "sympathetic cell"),
    ("neut-fg", "neut-bg", "text", "neutral cell/badge"),
    ("abs-fg", "abs-bg", "text", "absent cell"),
    ("ok-fg", "ok-bg", "text", "agree/add cell/badge"),
    ("warn-fg", "warn-bg", "text", "differ/disclaimer badge"),
    # ink used as fg on tinted backgrounds (.chip, .who, ins)
    ("ink", "crit-bg", "text", "chip on crit-bg"),
    ("ink", "symp-bg", "text", "chip on symp-bg"),
    ("ink", "neut-bg", "text", "chip on neut-bg"),
    ("ink", "abs-bg", "text", "chip on abs-bg"),
    ("ink", "ok-bg", "text", "ins (added prose) on ok-bg"),
    ("ink", "accent-wash", "text", "active pivot text"),
    # UI / non-text (3:1 per WCAG 1.4.11)
    ("accent", "paper", "ui", "focus outline / active underline"),
    ("control", "surface", "ui", "search input / pivot button border"),
    ("control", "paper", "ui", "control border on paper"),
    # NOTE: --line / --line-strong hairlines (table rules, list separators, card
    # edges) are purely decorative dividers -> exempt from 1.4.11, not gated here.
]

AA = {"text": 4.5, "large": 3.0, "ui": 3.0}


def main() -> int:
    print(f"{'pair':<34}{'ratio':>7}  {'need':>5}  status   note")
    print("-" * 92)
    fails = []
    for fg, bg, kind, note in PAIRS:
        r = contrast(fg, bg)
        need = AA[kind]
        ok = r >= need
        if not ok:
            fails.append((fg, bg, kind, note, r, need))
        mark = "PASS" if ok else "FAIL"
        print(f"{fg+' / '+bg:<34}{r:>6.2f}  {need:>5.1f}  {mark:<7}  [{kind}] {note}")
    print("-" * 92)
    if fails:
        print(f"\n{len(fails)} FAIL(s):")
        for fg, bg, kind, note, r, need in fails:
            print(f"  {fg} / {bg}: {r:.2f} < {need} ({note})")
        return 1
    print("\nAll pairs pass WCAG 2.1 AA.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
