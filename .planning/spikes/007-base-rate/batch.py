"""Spike 007 — base-rate run: analyze a designed slate through the L1 analyzer, sequentially.

Sequential (not parallel) because analyze.py holds a read-write DuckDB connection — one writer at a
time. Ordered light→heavy (by revision count) so results stream in. Per-article logs under logs/.

Roles: thesis (I-P) | clean control | forced-pivot control | cross-domain contested.
"""
import subprocess, sys, pathlib, re

TITLES = [
    ("Brontosaurus",                 "forced-pivot-control"),   # 2015 taxonomic revival
    ("Nakba",                        "thesis-IP"),
    ("Anti-Zionism",                 "thesis-IP"),
    ("Water",                        "clean-control"),
    ("Israeli–Palestinian conflict", "thesis-IP"),
    ("Chess",                        "clean-control"),
    ("Abortion",                     "cross-domain-contested"),
    ("Hamas",                        "thesis-IP"),
    ("Climate change",               "cross-domain-contested"),
]
ROOT = pathlib.Path(__file__).resolve().parents[3]   # gh-wiki
LOGDIR = pathlib.Path(__file__).resolve().parent / "logs"
LOGDIR.mkdir(parents=True, exist_ok=True)
ANALYZE = str(pathlib.Path(__file__).resolve().parents[1] / "005-analyzer" / "analyze.py")

for title, role in TITLES:
    slug = re.sub(r"[^a-z0-9]+", "_", title.lower()).strip("_")
    logf = LOGDIR / f"{slug}.log"
    if logf.exists() and "VERDICT:" in logf.read_text():   # resumable: skip already-completed
        print(f"\n### SKIP [{role}]: {title} (already done) ###", flush=True)
        continue
    print(f"\n### RUN [{role}]: {title} ###", flush=True)
    with open(logf, "w") as f:
        subprocess.run([sys.executable, ANALYZE, title], stdout=f, stderr=subprocess.STDOUT, cwd=str(ROOT))
    # echo the verdict line for live progress
    try:
        log = (LOGDIR / f"{slug}.log").read_text()
        v = [ln for ln in log.splitlines() if ln.startswith("VERDICT")]
        print(f"done: {title} — {v[0] if v else '(no verdict line)'}", flush=True)
    except Exception as e:
        print(f"done: {title} — (log read error {e})", flush=True)

print("\nALL DONE", flush=True)
