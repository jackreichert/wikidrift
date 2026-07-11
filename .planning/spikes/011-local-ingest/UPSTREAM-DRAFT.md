The XML dump parser loses nearly all of a revision's text whenever the wikitext contains XML entity
references — which real articles always do (`&lt;ref&gt;`, `&amp;`, `&quot;`, `&#nn;`, …). Only the final
run of character data (after the last entity in the revision) survives, and the entity characters
themselves (`<`, `>`, `&`) are dropped.

**Impact:** authorship output for real articles is ~1–4% of the true token count.

| Article (current revision) | `wikiwho-cli` all_tokens | hosted WikiWho / Python |
|---|---|---|
| Photosynthesis | 1,004 | 27,158 |
| Chess | ~1k | 45,589 |

**Minimal reproduction** (a single revision, correctly XML-escaped):

```xml
<mediawiki xmlns="http://www.mediawiki.org/xml/export-0.11/" version="0.11" xml:lang="en">
  <siteinfo><sitename>W</sitename><namespaces><namespace key="0" case="first-letter"/></namespaces></siteinfo>
  <page><title>T</title><ns>0</ns><id>1</id>
    <revision><id>1</id><timestamp>2020-01-01T00:00:00Z</timestamp>
      <contributor><username>A</username></contributor>
      <text xml:space="preserve">AAA words here. &lt;ref&gt;x&lt;/ref&gt; BBB words here.</text>
    </revision>
  </page>
</mediawiki>
```

`wikiwho-cli` emits only `bbb words here .` — "AAA words here." and the `<ref>…</ref>` are gone.

**Root cause:** in `src/dump_parser/mod.rs`, the `[MediaWiki, Page, Revision, Text(..)]` arm of the
`Event::Text` handler **overwrites** `revision_builder.text` with each text event
(`revision_builder.text = Some(Text::Normal(text.into_owned()))`). quick-xml (0.39) splits character data
into multiple `Event::Text` events at entity boundaries and emits each entity as a separate
`Event::GeneralRef`, which the parser doesn't handle. So only the last `Text` chunk is kept and the
entity content is discarded.

**Why CI didn't catch it:** the exact-parity tests are gated behind `python-diff` (skipped by default) and
their fixtures appear not to include inline entity references in revision text.

---

## PR

**Title:** fix(dump_parser): accumulate revision text across entity-split events

**Summary:** Accumulate every `Event::Text` chunk instead of overwriting, and handle `Event::GeneralRef`
by resolving numeric character references and the predefined XML entities and appending them. This makes
`<text>` reading whole again and restores parity with Python WikiWho.

**Diff:**
1. `Event::Text` for the text tag: `match &mut revision_builder.text { Some(Text::Normal(s)) => s.push_str(&text), _ => … }`.
2. New `Event::GeneralRef` arm (only inside `Text(false, _)`): `resolve_char_ref()` for `&#..;`, else
   `quick_xml::escape::resolve_predefined_entity(name)` for `lt/gt/amp/quot/apos`; append the result.
3. Regression coverage added in `tests/parser_tests.rs` for entity-split text + `GeneralRef` handling.
4. User-facing changelog entry added under `CHANGELOG.md` -> `## [Unreleased]` -> `### Fixed`.

**Validation** (neutral articles, current revision, local `wikiwho-cli` vs hosted/Python WikiWho):
Photosynthesis **27,158 = 27,158** (exact), Chess **45,589 = 45,589** (exact), Water **48,368** vs 48,369
(off by 1). The minimal repro now yields `aaa words here . < ref > x < / ref > bbb words here .` Existing
`cargo test` suite still green.

**Regression test added:** `tests/parser_tests.rs` now parses the minimal dump above and asserts the parsed
revision text contains both `AAA` and `BBB` and the `<`/`>` characters, guarding this failure mode even when
`python-diff` parity suites are not enabled.

**Notes for the maintainer:** named non-predefined entities (e.g. a literal `&nbsp;` in wikitext) are not a
concern here because MediaWiki dumps escape wikitext `&` as `&amp;`, so `&nbsp;` arrives as `&amp;nbsp;` →
resolves to the text `&nbsp;`, not a general ref. Only the five predefined entities + numeric char refs can
appear as `GeneralRef` in dump text.
