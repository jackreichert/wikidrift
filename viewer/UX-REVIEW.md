# WikiDrift Viewer — UX & Accessibility Review

**Date:** 2026-07-08 · **Method:** two independent, isolated reviewers (impeccable-style: design-director + a11y/technical), synthesized. Browser-overlay + `npx impeccable detect` not available in-env, so this is the two-assessment LLM review, not the automated detector.

**Scores:** Nielsen design health **28/40** (solid). Technical audit **12/20** (acceptable, significant work needed): A11y 2 · Perf 2 · Responsive 2 · Theming 3 · Anti-patterns 3. Cognitive load **4/8 failed → critical**, concentrated in the Diff tab.

**Anti-slop verdict: PASS.** Reads as "a researcher built this," not "AI made this." No gradient text / glass / hero-metrics; framework-free, restrained palette.

## What's working (keep)
1. **"Lead, not a verdict" ethic executed consistently** — disclaimer chip, footer, nuanced copy ("controversy ≠ malice"). The hardest part of the brief, done well.
2. **Framework-free discipline** — one cached CSS, system fonts, zero external deps, semantic HTML.
3. **Provenance + non-color redundancy** — receipts link exact `oldid`s; stance/verdict carry text labels, not just color.

## Prioritized findings

### P0
- **Diff: 1.5 MB / mid-word wrap / color-only / SR-hostile.** `difflib.HtmlDiff(wrapcolumn=90)` hard-wraps mid-word, bloats Zionism to ~1.5 MB (~3,600 rows, 51,753 `&nbsp;`), distinguishes add/remove by background **color alone** (no `+`/`−`), and reads as gibberish to screen readers. **Fix:** drop `difflib`; render the already-computed `chunks` into the (currently unused) `.drow`/`.dl`/`.dr` side-by-side grid with `+`/`−` markers + `<caption>`/`scope`. One change fixes weight + color-only + wrapping + a11y. Authorship stays in the collapsible.
- **Contrast fails AA on every article.** Computed vs white: `absent` **1.5:1 (invisible)**, `neutral` 3.5, verdict `differ` 2.8, `insufficient` 2.6; greys `.count` ~2.85, `.pv span` ~4.48. **Fix:** darken `STANCE_COLOR`/`VERDICT` hexes; dark text on the light `absent` cell.

### P1
- **Wide tables overflow on mobile.** Only `.diffwrap` scrolls; stance grid / facts / receipts overflow the viewport on phones. **Fix:** wrap those in an `overflow-x:auto` div (`.tablewrap`).

### P2
- **No `:focus-visible`; no tab/chip ARIA; filter state color-only.** **Fix:** add focus rings; `role=tab/tabpanel`/`aria-selected` in `tabs()`, `aria-pressed` on chips, `aria-live` on the count.
- **Internal jargon leaks into a lay UI** — `L5 #1`, `L3`, `corroborator`, pivot "weight" (PWR), "focal", "NPOV-departure", `he/ar`. **Fix:** relabel / gloss; de-jargon card headlines + M-score copy.
- **"Framing" is a harsh default tab** — opens on the reddest, most abstract view (grid of `crit` cells), subtly reads accusatory and is hardest for laypeople. Article page also never shows its own plain headline (computed, but only used on index cards). **Fix:** surface the headline on the page; default to Diff or a plain Overview.

### P3
- Heading gaps (Diff/Receipts panels + index have no `h2`); table `scope`/`caption`; dead `.drow`/`.authdiff` CSS (reuse per P0); index sort/severity/pagination for eventual scale beyond hundreds.

## Diff approach decision
Two flavors for the P0 diff fix:
- **Compact side-by-side from `chunks`** (recommended) — clean before│after columns, red/green + `+`/`−`, no inline editor tags (authors in the collapsible). Readable + light (~350 KB) + accessible. Coarser (run-level) granularity.
- **Keep difflib, remove `wrapcolumn`** — prettier per-character highlight, but still ~1 MB + a11y gaps.

## Design direction (visual elevation) — separate pass
Brief: **professional, restrained, not overly designed, must not look like an out-of-the-box UI toolkit.** Direction + implementation tracked separately (impeccable design pass). Constraints hold: static, framework-free, no external fonts/CDNs.

## Implemented — impeccable product-register pass (2026-07-08)
Register = **product** (professional/restrained, distinctiveness through tuning, not editorial flourish). Full `build.py` rewrite of the presentation layer.

- **P0 diff:** replaced `difflib.HtmlDiff` with a compact side-by-side from `chunks` (`diff_rows` + `_text_chunks`). Zionism **1.5 MB → 113 KB**; Warsaw 316 KB → 77 KB. `+`/`−` markers (not color-only); `role="table"`; authorship moved to a `<details>`.
- **P0 contrast:** OKLCH design tokens; stance/verdict now tinted-bg + dark same-hue text via CSS classes (`sc-*`, `v-*`) — the invisible `absent` cell and the sub-AA `neutral`/`differ`/`insufficient` badges are fixed.
- **Palette/look:** OKLCH tinted neutrals + a `--chrome` second layer + one slate-indigo accent (`--accent`); retired the generic `#4457a5`. Findings **list** (hairline rows) replaces the card grid; underline filters replace pills; quieter typographic `.lead` replaces the blue alert blurbs; 7px radii, hairlines.
- **P1 responsive:** `.tablewrap{overflow-x:auto}` around every grid/table; diff stacks to one column ≤640px; dropped the buggy `100vw` full-bleed.
- **P2 a11y:** global `:focus-visible`; tabs are `role=tablist/tab` + `aria-selected`; filter chips `aria-pressed`; table `scope`; `aria-label` on diff.
- **P2 jargon/IA:** plain h2s + plain-language `headline()` (no `he/ar`/`L1 pivot`), removed `L5 #1`/`L3`/`corroborator` tags, M-score copy → "how much was it fought over", and each article opens with its own plain **summary** sentence (addresses the "harsh Framing default").

### Update (2026-07-08, diff overhaul)
- Diff root-cause fixed: was diffing WikiWho **raw-wikitext tokens** (citation/template soup) → now diffs **stripped prose**. Replaced fragment view with an in-context **redline** (`<del>`/`<ins>`) that reads like the article. **Per-pivot pages** (`<slug>.p<i>.html`) — main Zionism page **750 KB → 40 KB**. **Authorship** annotated inline (insertions colored by editor, best-match; hover for who). **Blame tab removed** (redundant with redline).

### Still open (v-next)
- Verify computed contrast ratios on the final OKLCH values (target AA 4.5:1) — approach is light-bg/dark-text so expected to pass; confirm with a checker.
- Evidence quotes still `title=`-only (keyboard/touch-inaccessible) — P3.
- Index: sort / severity cue / pagination for scale beyond a few hundred — later.
- Consider Diff (concrete) as the default tab vs Framing — summary lead now mitigates.
- Deploy: repo-layout decision (separate public repo vs `/docs`) + `drift.encyclopediae.org` CNAME.
