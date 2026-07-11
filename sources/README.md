# Prior art & sources

Working notes on the literature and tools WikiDrift builds on or deliberately avoids.
Compiled from the vault prior-art survey (`wikipedia-drift-prior-art`) plus paper abstracts / publisher pages.

**How to use this folder.** Each file is a short, project-oriented summary: what the work claims, how it works, and what it means for WikiDrift. Prefer the primary links for citation; these notes are not substitutes for the papers.

## Landscape (one line)

Every **component** exists; the empty cell is the **composition**: unsupervised pivot discovery from *content displacement* + provenance attribution of that pivot + external-reference layers for born-framed cases.

| Cell | Examples | Relation |
|---|---|---|
| List-first / social graph | ADL *Editing Hate*; Heritage Project Esther | Approach **rejected** (circular) |
| Token provenance | WikiWho, TokTrack, wikiwho_rs, Who Wrote That | **Engine** |
| Content survival | WikiTrust; Halfaker PWR | **L1 metric** |
| Temporal / framing | Mind Your POV; Editing Bursts | Nearest neighbors (different change-point) |
| Controversy | Yasseri / Sumi edit-wars, M-score | **Context only** |
| Cross-lingual | Omnipedia, Manypedia, MultiWiki, InfoGap | **L5 ancestors** |
| Bias measurement | Greenstein–Zhu; Yang–Colavizza | Cautions + citation-slant ideas |
| Method guidance | Johnson et al. NPOV practices (2025) | L2 design constraint |
| Case studies | Grabowski–Klein (2023) | Benchmark / L5-gap motivation |

## Core papers (summaries in this dir)

### Provenance & displacement (building blocks)
- [wikiwho-2014.md](wikiwho-2014.md) — Flöck & Acosta, *WikiWho* (WWW 2014)
- [toktrack-2017.md](toktrack-2017.md) — Flöck, Erdogan & Acosta, *TokTrack* (ICWSM 2017)
- [wikitrust-2007.md](wikitrust-2007.md) — Adler & de Alfaro, content-driven reputation (WWW 2007)
- [halfaker-pwr-2009.md](halfaker-pwr-2009.md) — Halfaker et al., persistent-word-revisions (WikiSym 2009)
- [wikiwho-rs.md](wikiwho-rs.md) — `wikiwho_rs` local engine (tooling)

### Temporal / framing / controversy
- [mind-your-pov-2018.md](mind-your-pov-2018.md) — Pavalanathan, Han & Eisenstein (CSCW 2018)
- [yasseri-edit-wars-2011.md](yasseri-edit-wars-2011.md) — Sumi, Yasseri et al., edit wars / M-score line
- [johnson-npov-2025.md](johnson-npov-2025.md) — Recommended practices for NPOV research

### Cross-lingual & external reference
- [omnipedia-2012.md](omnipedia-2012.md) — Bao, Hecht et al. (CHI 2012)
- [manypedia-2012.md](manypedia-2012.md) — Massa & Scrinzi (WikiSym 2012)
- [infogap-2024.md](infogap-2024.md) — Samir et al., narrative inconsistencies (EMNLP 2024)
- [reference-reliability-2023.md](reference-reliability-2023.md) — Baigutanova et al., cross-edition sources (CIKM 2023)

### Bias measurement & cautions
- [greenstein-zhu.md](greenstein-zhu.md) — AER 2012 + MISQ 2018 slant studies
- [yang-colavizza-2024.md](yang-colavizza-2024.md) — News-source polarization on Wikipedia
- [grabowski-klein-2023.md](grabowski-klein-2023.md) — Holocaust-history distortion on enwiki

### Motivating / adjacent (not method inputs)
- [adjacent-advocacy-and-tools.md](adjacent-advocacy-and-tools.md) — ADL, Heritage, tooling (XTools, WikiBlame, dumps), other citations

## Primary index sources in-repo / vault

- Design: methodology section of the static site / `viewer/templates/methodology.html`
- Vault: `wikipedia-drift-prior-art.md`, `wikipedia-bias-evidence-findings.md`
