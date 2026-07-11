# wikiwho_rs v0.3.4 — markup causes catastrophic content loss

**Status: ROOT-CAUSED AND FIXED (2026-07-07).** Bug was in the XML **dump parser** (not the algorithm).
Fix = `wikiwho_rs-text-accumulate.patch` (this dir), applied to the vendored crate. After the fix the local
engine matches the hosted API on neutral articles: **Photosynthesis 27,158 = 27,158 (exact)**, **Chess
45,589 = 45,589 (exact)**, Water 48,368 vs 48,369. The local "wikiwho_rs on dumps" path is now viable for
L4 corpus-scale batch.

> The vendored crate is gitignored (own .git), so the fix lives only in the working copy. To restore it after
> a fresh clone: `cd wikiwho_rs && git apply ../../011-local-ingest/wikiwho_rs-text-accumulate.patch`.

## The fix

In `src/dump_parser/mod.rs`, the `<text>` reader **overwrote** `revision_builder.text` on every
`Event::Text`, and **ignored** `Event::GeneralRef` entirely. quick-xml (0.39) splits character data into
multiple `Text` events at each entity reference and emits the entity as a separate `GeneralRef` event. So
only the final `Text` chunk (after the last entity) survived, and the entity characters (`< > &`) were lost.
Fix: (1) **accumulate** each `Text` chunk (`push_str`) instead of overwriting; (2) handle `GeneralRef` —
resolve numeric char refs (`resolve_char_ref`) + predefined entities (`resolve_predefined_entity`) and append.

---

## Original symptom (pre-fix, for the record)

## Symptom

Local `wikiwho-cli` under-tokenizes real Wikipedia articles by ~95–99%:

| Article | local `wikiwho-cli` (pre-fix) | hosted WikiWho API |
|---|---|---|
| Photosynthesis | **1,004** | **27,158** |
| Chess | **~1,000** | **45,589** |

The surviving tokens are always the article's *tail* (bibliography / navboxes / categories).

## Root cause (minimal repro)

**Any `<...>` markup — HTML tag OR comment — makes the analysis drop all content before it**, keeping
only text after the last markup token. Single revision, so no diffing/dedup across revisions is involved.

```
input                          wikiwho-cli all_tokens
"AAA. <ref>x</ref> BBB."     → "bbb words here ."          (AAA + ref dropped)
"AAA. BBB. <ref>x</ref>"     → "/ ref"                     (AAA + BBB dropped)
"AAA. <b>x</b> BBB."         → "bbb words here ."          (any tag, not just <ref>)
"AAA. <!--c--> BBB."         → "bbb words here ."          (comments too)
"AAA. BBB. CCC."             → "aaa ... bbb ... ccc ..."   (no markup → all kept ✓)
```

## Localization

- **Tokenizer/splitters are correct.** `split_into_paragraphs_naive` / `split_into_sentences_naive` /
  `split_into_tokens_naive` on the repro produce all 4 paragraphs, all sentences, all tokens
  (incl. "Alpha" and pre-`<ref>` "Beta paragraph cites something."). See `snapshot-tokens/examples/trace.rs`.
- So the loss is inside **`PageAnalysis::analyse_page`** graph assembly (paragraph/sentence insertion),
  triggered by tokens containing/adjacent to `<` `>`.
- Both `optimized-str` (default) and `--no-default-features` (naive) builds behave identically → not a
  feature-flag issue.
- Not scale: 1000 synthetic paragraphs → 12,000 tokens ✓; one 2000-word paragraph → 2,000 tokens ✓.

## Reproduce

```sh
cd snapshot-tokens && cargo run --release --example trace     # shows splits are correct
# then feed the razor inputs above to ../001b-.../wikiwho_rs/target/release/wikiwho-cli
```

## Consequence for this project

- The spikes never caught this: `001b` ran local only on **Bioglass 45S5**, which is now a redirect stub
  (16 tokens, no `<ref>`), and the actual deleted-token-lifecycle result used the **hosted** API.
- Benchmark ingestion uses the hosted path (`wikidrift.provenance.build_snapshots`) — validated, and
  hosted covers all 8 must-flag articles.
- Local wikiwho_rs is still wanted for L4 corpus-scale batch → fix or file upstream (github.com/Schuwi/wikiwho_rs)
  before relying on it. This file is a ready-made repro to attach.
