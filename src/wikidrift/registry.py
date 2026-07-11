"""Cross-layer per-article study config — the single home for study config more than one layer reads.

Today that is FOCAL (which entities to watch): L2 stance (stance.py) and L5 cross-lingual framing
(l5_crosslingual.py) BOTH classify the same entities, but l5_crosslingual imports stance (an import cycle),
so they couldn't share the list directly — it lived duplicated in both, and the two copies had already
drifted (same entity SETS, different order). One definition here, imported by both, ends that.

Single-CONSUMER per-article config stays with its consumer (no cross-module divergence risk): l5_crosslingual
owns SLATE and FALLBACK_PIVOT, l5_factcheck owns QUESTIONS, benchmark owns ROSTER (the ground-truth oracle),
and the viewer owns CATEGORY (kept package-decoupled by design).

Order below is the site-canonical order the viewer renders; it is display-only — every stance computation is
per-entity and order-independent, so the two former orderings differed only cosmetically.
"""

# Focal entities per article (transparent, researcher-editable). Shared by L2 stance + L5 framing.
FOCAL = {
    "Nakba": ["Israel", "Palestinians", "Zionism"],
    "Zionism": ["Israel", "Palestinians", "Zionism"],
    "Photosynthesis": ["Plant", "Sunlight"],
    "Warsaw concentration camp": ["Poland", "Germany", "Jews"],
    "Hamas": ["Hamas", "Israel"],
    "Israeli–Palestinian conflict": ["Israel", "Palestinians"],
    "Palestinian political violence": ["Palestinians", "Israel"],
    "Gaza war": ["Israel", "Hamas", "Palestinians"],
    "Jedwabne pogrom": ["Poland", "Germany", "Jews"],
    "Naliboki massacre": ["Poland", "Jews"],
    "Rescue of Jews by Poles during the Holocaust": ["Poland", "Jews"],
    # --- Session 08 ---
    "Palestine": ["Israel", "Palestinians", "Zionism"],
    "UNRWA": ["UNRWA", "Israel", "Palestinians"],
    "Anti-Zionism": ["Zionism", "Israel", "Jews"],
    "Collaboration in German-occupied Poland": ["Poland", "Germany", "Jews"],
    "History of Zionism": ["Zionism", "Israel", "Palestinians"],
    "Genetic studies of Jews": ["Jews", "Israel"],
    "Racial conceptions of Jewish identity in Zionism": ["Zionism", "Jews"],
    "Bar Kokhba Revolt": ["Jews", "Romans", "Bar Kokhba"],
    "Gaza genocide": ["Israel", "Palestinians", "Hamas"],
    # L2-only benign-rewrite control (was in stance.FOCAL, not studied cross-lingually): a FLAT trajectory
    # for these entities is the expected low-false-positive signal.
    "Climate change": ["fossil fuel industry", "climate scientists", "governments"],
}

# The fallback when an article isn't in FOCAL — a neutral I-P pair (both stance and crosslingual used it).
DEFAULT_FOCAL = ["Israel", "Palestinians"]
