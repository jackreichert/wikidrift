"""Lossless migration from the canonical corpus into article-owned shards."""
import json
import pathlib
import tempfile
import unittest
from unittest import mock

import duckdb

from wikidrift import cli, provenance, shards


class ArticleShardMigration(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.data_dir = pathlib.Path(self._tmp.name) / "data"
        self.data_dir.mkdir()
        self.db_path = self.data_dir / "provenance.duckdb"

        con = duckdb.connect(str(self.db_path))
        provenance.ensure_schema(con)
        con.execute("CREATE TABLE snap(article TEXT, marker INTEGER)")
        con.execute("CREATE TABLE snapshots(article TEXT, marker INTEGER)")
        con.executemany("INSERT INTO revisions VALUES (?,?,?,?)", [
            ("Alpha", 1, "2020-01-01T00:00:00Z", "EditorA"),
            ("Beta / Gamma", 2, "2021-01-01T00:00:00Z", "EditorB"),
        ])
        con.executemany("INSERT INTO rsnap VALUES (?,?,?,?,?)", [
            ("Alpha", "2020-01-01", 1, 10, 1),
            ("Alpha", "2020-01-01", 1, 11, 1),
            ("Beta / Gamma", "2021-01-01", 2, 20, 2),
        ])
        provenance.record_article_identity(
            con, provenance.ResolvedArticle("Alpha alias", "Alpha", 101),
        )
        con.execute("INSERT INTO snap VALUES ('Alpha', 1)")
        con.execute("INSERT INTO snapshots VALUES ('Beta / Gamma', 2)")
        con.close()

        findings = self.data_dir / "findings"
        findings.mkdir()
        (findings / "Alpha.profile.json").write_text('{"article":"Alpha"}', encoding="utf-8")
        (findings / "Demo_Topic.receipts.json").write_text('{"article":"Demo Topic"}', encoding="utf-8")
        (findings / "divergence.json").write_text('{"static":{}}', encoding="utf-8")

        mscore = self.data_dir / "mscore"
        mscore.mkdir()
        (mscore / "Alpha.json").write_text("[]", encoding="utf-8")

        history = self.data_dir / "history-xml"
        history.mkdir()
        (history / "Beta___Gamma.xml").write_text("<mediawiki />", encoding="utf-8")

        logs = self.data_dir / "logs" / "analysis-2026-07-30"
        logs.mkdir(parents=True)
        (logs / "Alpha.pipeline.log").write_text("complete", encoding="utf-8")
        (logs / "batch-summary.log").write_text("complete", encoding="utf-8")

    def test_migrates_database_rows_and_artifacts_without_removing_sources(self):
        source_profile = self.data_dir / "findings" / "Alpha.profile.json"
        source_bytes = source_profile.read_bytes()

        report = shards.migrate_all(self.data_dir)

        self.assertEqual(report["article_count"], 3)
        self.assertTrue(self.db_path.exists())
        self.assertEqual(source_profile.read_bytes(), source_bytes)

        alpha_dir = self.data_dir / "articles" / "Alpha"
        alpha = duckdb.connect(str(alpha_dir / "provenance.duckdb"), read_only=True)
        self.assertEqual(alpha.execute("SELECT count(*) FROM revisions").fetchone()[0], 1)
        self.assertEqual(alpha.execute("SELECT * FROM revisions").fetchone(),
                 ("Alpha", 1, "2020-01-01T00:00:00Z", "EditorA"))
        self.assertEqual(alpha.execute("SELECT count(*) FROM rsnap").fetchone()[0], 2)
        self.assertEqual(alpha.execute("SELECT count(*) FROM snap").fetchone()[0], 1)
        self.assertEqual(alpha.execute("SELECT count(*) FROM snapshots").fetchone()[0], 0)
        self.assertEqual(
            alpha.execute("SELECT requested_title, canonical_title FROM article_identity").fetchone(),
            ("Alpha alias", "Alpha"),
        )
        alpha.close()

        self.assertTrue((alpha_dir / "findings" / "Alpha.profile.json").exists())
        self.assertTrue((alpha_dir / "mscore" / "Alpha.json").exists())
        self.assertTrue((alpha_dir / "logs" / "analysis-2026-07-30" / "Alpha.pipeline.log").exists())

        beta_dir = self.data_dir / "articles" / "Beta___Gamma"
        beta = duckdb.connect(str(beta_dir / "provenance.duckdb"), read_only=True)
        self.assertEqual(beta.execute("SELECT count(*) FROM revisions").fetchone()[0], 1)
        self.assertEqual(beta.execute("SELECT count(*) FROM snapshots").fetchone()[0], 1)
        beta.close()
        self.assertTrue((beta_dir / "history-xml" / "Beta___Gamma.xml").exists())

        self.assertTrue((self.data_dir / "articles" / "Demo_Topic" / "findings" /
                         "Demo_Topic.receipts.json").exists())
        self.assertTrue((self.data_dir / "articles" / "_shared" / "findings" / "divergence.json").exists())
        self.assertTrue((self.data_dir / "articles" / "_shared" / "logs" /
                         "analysis-2026-07-30" / "batch-summary.log").exists())

    def test_rerun_rebuilds_the_same_verified_shards(self):
        first = shards.migrate_all(self.data_dir)
        second = shards.migrate_all(self.data_dir)

        self.assertEqual(second["article_count"], first["article_count"])
        manifest_path = self.data_dir / "articles" / "Alpha" / "migration-manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        self.assertEqual(manifest["tables"]["rsnap"], 2)
        self.assertIn("findings/Alpha.profile.json", manifest["artifacts"])

        alpha = duckdb.connect(
            str(self.data_dir / "articles" / "Alpha" / "provenance.duckdb"), read_only=True
        )
        self.assertEqual(alpha.execute("SELECT * FROM revisions").fetchall(),
                         [("Alpha", 1, "2020-01-01T00:00:00Z", "EditorA")])
        alpha.close()

    def test_checksum_mismatch_aborts_artifact_copy(self):
        real_sha256 = shards._sha256
        calls = {"count": 0}

        def mismatched_target(path):
            calls["count"] += 1
            digest = real_sha256(path)
            return digest if calls["count"] % 2 else "mismatch"

        with mock.patch.object(shards, "_sha256", side_effect=mismatched_target):
            with self.assertRaisesRegex(RuntimeError, "checksum verification failed"):
                shards.migrate_all(self.data_dir)

    def test_cli_dispatches_migration_command(self):
        with mock.patch.object(shards, "migrate_all", return_value={"article_count": 3}) as migrate:
            cli.main(["migrate-shards", "--source-data-dir", str(self.data_dir)])

        migrate.assert_called_once_with(self.data_dir, None)


if __name__ == "__main__":
    unittest.main()