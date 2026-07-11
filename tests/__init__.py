"""Test suite for wikidrift (stdlib unittest — no external deps).

Run:  python -m unittest discover -s tests
This package __init__ puts the package (src/) and the viewer on sys.path so the tests import
`wikidrift` and `check_contrast` without an editable install.
"""
import pathlib
import sys

_ROOT = pathlib.Path(__file__).resolve().parents[1]
for _p in (_ROOT / "src", _ROOT / "viewer"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))
