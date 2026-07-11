"""Corpus — a thin repository over the DuckDB token corpus (revisions + rsnap).

The query shapes the domain needs, named and in one place, so a schema change (e.g. renaming
`rsnap.o_rev_id`) is a single-file edit instead of Shotgun Surgery across every module that open-codes the
same SELECTs. Wraps an existing connection (`Corpus(con)`) — connection lifetime stays with the caller;
collapsing the scattered `duckdb.connect` sites into a `Corpus.open()` factory is a later step.

Read/write boundary: this is the READ model. Every domain read now goes through it — drift, prerank, l4,
pipeline, bootstrap, cli, stance, l5_sources, viewer/export_l3 (and the duplicated snapshot-count existence
check, once copy-pasted into four modules). The WRITE/populate side stays in provenance (schema DDL + hosted
snapshot building) and ingest (the transactional rsnap load) — a repository owns neither migrations nor writes.

Remaining polish (deferred, low value / higher churn): a `Corpus.open(read_only=...)` factory to fold the ~16
`duckdb.connect(str(config.DB), ...)` sites into one — callers currently pass the raw `con` widely, so that's
a separate pass.
"""


class Corpus:
    def __init__(self, con):
        self.con = con

    # --- rsnap (persistent-revision snapshots) ------------------------------
    def snapshots(self, article):
        """[(snap_date, snap_rev)] in time order (distinct)."""
        return self.con.execute(
            "SELECT DISTINCT snap_date, snap_rev FROM rsnap WHERE article=? ORDER BY snap_date, snap_rev",
            [article]).fetchall()

    def membership_rows(self, article):
        """(snap_date, snap_rev, token_id) rows, grouped by snapshot in time order (PWR membership source)."""
        return self.con.execute(
            "SELECT snap_date, snap_rev, token_id FROM rsnap WHERE article=? ORDER BY snap_date, snap_rev",
            [article]).fetchall()

    def latest_snapshot(self, article):
        """(snap_date, snap_rev) of the most recent snapshot, or None."""
        return self.con.execute(
            "SELECT snap_date, snap_rev FROM rsnap WHERE article=? ORDER BY snap_date DESC LIMIT 1",
            [article]).fetchone()

    def latest_snap_rev(self, article):
        """(snap_rev,) of the most recent snapshot, or None."""
        return self.con.execute(
            "SELECT snap_rev FROM rsnap WHERE article=? ORDER BY snap_date DESC LIMIT 1", [article]).fetchone()

    def snapshot_token_ids(self, article, snap_rev):
        """The set of token_ids present in one snapshot."""
        return {r[0] for r in self.con.execute(
            "SELECT token_id FROM rsnap WHERE article=? AND snap_rev=?", [article, snap_rev]).fetchall()}

    def snapshot_tokens(self, article, snap_rev):
        """[(token_id, o_rev_id)] of one snapshot."""
        return self.con.execute(
            "SELECT token_id, o_rev_id FROM rsnap WHERE article=? AND snap_rev=?", [article, snap_rev]).fetchall()

    def snapshot_o_rev_ids(self, article, snap_rev):
        """[(o_rev_id,)] of one snapshot (profile reads the origin revs)."""
        return self.con.execute(
            "SELECT o_rev_id FROM rsnap WHERE article=? AND snap_rev=?", [article, snap_rev]).fetchall()

    def snapshot_count(self, article):
        """Number of distinct persistent-revision snapshots — the ≥3 existence gate that was copy-pasted
        into pipeline / bootstrap / l4 / ingest."""
        return self.con.execute(
            "SELECT count(DISTINCT snap_rev) FROM rsnap WHERE article=?", [article]).fetchone()[0]

    def articles_with_snapshots(self, min_snaps=3):
        """Articles with at least `min_snaps` snapshots — the default target set for the offline verbs."""
        return [r[0] for r in self.con.execute(
            "SELECT article FROM rsnap GROUP BY article HAVING count(DISTINCT snap_rev) >= ? ORDER BY article",
            [min_snaps]).fetchall()]

    # --- revisions (the Action-API timeline) --------------------------------
    def size_series(self, article):
        """[(ts, size, user)] per revision joined to its byte size, time-ordered (the pre-ranker's input)."""
        return self.con.execute(
            "SELECT r.ts, z.size, r.user FROM revisions r JOIN rev_size z ON z.article=r.article "
            "AND z.rev_id=r.rev_id WHERE r.article=? ORDER BY r.ts", [article]).fetchall()

    def distinct_articles(self):
        """Every article with a revision timeline (whole-corpus sweeps)."""
        return [r[0] for r in self.con.execute(
            "SELECT DISTINCT article FROM revisions ORDER BY article").fetchall()]

    def revision_count(self, article):
        """Number of revisions in the timeline."""
        return self.con.execute("SELECT count(*) FROM revisions WHERE article=?", [article]).fetchone()[0]

    def first_revision_ts(self, article):
        """Earliest revision timestamp, or None."""
        return self.con.execute("SELECT min(ts) FROM revisions WHERE article=?", [article]).fetchone()[0]

    def revision_rows(self, article):
        """[(rev_id, ts, user)] for every revision (the full timeline, when a caller needs all three)."""
        return self.con.execute(
            "SELECT rev_id, ts, user FROM revisions WHERE article=?", [article]).fetchall()

    # --- revisions (the Action-API timeline) --------------------------------
    def revisions_between(self, article, start_ts, end_ts):
        """[(rev_id, ts, user)] with start_ts < ts <= end_ts, in time order (the refine window)."""
        return self.con.execute(
            "SELECT rev_id, ts, user FROM revisions WHERE article=? AND ts>? AND ts<=? ORDER BY ts",
            [article, start_ts, end_ts]).fetchall()

    def revision_ts(self, article):
        """{rev_id: ts} for every revision of the article."""
        return dict(self.con.execute("SELECT rev_id, ts FROM revisions WHERE article=?", [article]).fetchall())

    def revision_editor(self, article):
        """{rev_id: user} for every revision of the article."""
        return dict(self.con.execute("SELECT rev_id, user FROM revisions WHERE article=?", [article]).fetchall())
