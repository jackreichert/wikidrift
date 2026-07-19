"""wikidrift — an editor-agnostic, temporal narrative-drift detector for Wikipedia.

Reads an article against its OWN edit history to surface where a long-stable narrative was rewritten
(L1, PWR-grounded), pre-ranks candidates from metadata alone (prerank), and measures directional
framing shifts with an LLM stance classifier (L2). Where the internal engine is structurally blind
(born-biased / long-stable bias), L5 compares against EXTERNAL references through a cross-language
stance comparison (l5_crosslingual), a lead comparison (l5_framing_lite), and cross-edition
fact/citation divergence (l5_factcheck). M-score
(mscore) is a metadata-only controversy corroborator. Every output is a LEAD for a researcher, never
a published verdict — a change detector, not a bias detector (base-rate finding).

Promoted from .planning/spikes/ (001a/b, 005, 008, 009, 010 = L1/L2/bench; 012a/b/c = L5 #1;
013 = M-score; 014 = L5 #2), which remain the frozen record.
Design: ~/Documents/JackObsidian/encyclopediae/wikipedia-filter-mirror-design.md
"""
__version__ = "0.1.0"

from . import (config, provenance, drift, prerank, stance, benchmark,  # noqa: F401
               l5_crosslingual, l5_factcheck, lexical, mscore, ingest, pipeline, llm)
