from __future__ import annotations

import shutil
import unittest
from pathlib import Path

from vn_event_dw.etl import open_connection, reload_fb_posts, run_etl


class FbPostFolderIngestTests(unittest.TestCase):
    def test_run_etl_reads_multiple_fb_post_csvs_from_folder(self) -> None:
        temp_root = Path.cwd() / "_tmp_fb_posts_ingest_test"
        db_path = temp_root / "warehouse.db"
        input_dir = temp_root / "input"
        fb_posts_dir = input_dir / "fb_posts"

        shutil.rmtree(temp_root, ignore_errors=True)
        fb_posts_dir.mkdir(parents=True, exist_ok=True)

        try:
            (fb_posts_dir / "mobile_legends_2026_06.csv").write_text(
                "source_post_id,fb_page_id,post_time,post_content\n"
                "post_ml_001,1722990624697290,2026-06-01T08:30:00+07:00,Mobile Legends update is live!\n",
                encoding="utf-8",
            )
            (fb_posts_dir / "crossfire_2026_06.csv").write_text(
                "source_post_id,fb_page_id,post_time,post_content\n"
                "post_cf_001,468000023324394,2026-06-02T09:00:00+07:00,CrossFire season patch released today.\n",
                encoding="utf-8",
            )

            stats = run_etl(
                db_path=db_path,
                config_path=Path("examples/config.json"),
                input_dir=input_dir,
            )

            self.assertEqual(stats.raw_fb_posts, 2)

            conn = open_connection(db_path)
            try:
                raw_count = conn.execute("SELECT COUNT(*) FROM raw_fb_posts").fetchone()[0]
                distinct_files = conn.execute("SELECT COUNT(DISTINCT source_file) FROM raw_fb_posts").fetchone()[0]

                self.assertEqual(raw_count, 2)
                self.assertEqual(distinct_files, 2)
            finally:
                conn.close()
        finally:
            shutil.rmtree(temp_root, ignore_errors=True)

    def test_reload_fb_posts_replaces_existing_fb_rows(self) -> None:
        temp_root = Path.cwd() / "_tmp_fb_posts_reload_test"
        db_path = temp_root / "warehouse.db"
        input_dir = temp_root / "input"
        fb_posts_dir = input_dir / "fb_posts"

        shutil.rmtree(temp_root, ignore_errors=True)
        fb_posts_dir.mkdir(parents=True, exist_ok=True)

        try:
            first_file = fb_posts_dir / "mobile_legends_2026_06.csv"
            second_file = fb_posts_dir / "crossfire_2026_06.csv"

            first_file.write_text(
                "source_post_id,fb_page_id,post_time,post_content\n"
                "post_ml_001,1722990624697290,2026-06-01T08:30:00+07:00,Mobile Legends update is live!\n",
                encoding="utf-8",
            )
            second_file.write_text(
                "source_post_id,fb_page_id,post_time,post_content\n"
                "post_cf_001,468000023324394,2026-06-02T09:00:00+07:00,CrossFire season patch released today.\n",
                encoding="utf-8",
            )

            run_etl(
                db_path=db_path,
                config_path=Path("examples/config.json"),
                input_dir=input_dir,
            )

            second_file.unlink()

            stats = reload_fb_posts(
                db_path=db_path,
                config_path=Path("examples/config.json"),
                input_dir=input_dir,
            )
            self.assertEqual(stats.raw_fb_posts, 1)

            conn = open_connection(db_path)
            try:
                raw_count = conn.execute("SELECT COUNT(*) FROM raw_fb_posts").fetchone()[0]
                distinct_files = conn.execute("SELECT COUNT(DISTINCT source_file) FROM raw_fb_posts").fetchone()[0]

                self.assertEqual(raw_count, 1)
                self.assertEqual(distinct_files, 1)
            finally:
                conn.close()
        finally:
            shutil.rmtree(temp_root, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
