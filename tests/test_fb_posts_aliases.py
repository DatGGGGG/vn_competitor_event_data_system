from __future__ import annotations

import shutil
import unittest
from pathlib import Path

from vn_event_dw.etl import open_connection, run_etl


class FbPostAliasTests(unittest.TestCase):
    def test_run_etl_accepts_richer_fb_export_headers(self) -> None:
        temp_root = Path.cwd() / "_tmp_fb_posts_alias_test"
        db_path = temp_root / "warehouse.db"
        input_dir = temp_root / "input"
        fb_posts_dir = input_dir / "fb_posts"

        shutil.rmtree(temp_root, ignore_errors=True)
        fb_posts_dir.mkdir(parents=True, exist_ok=True)

        try:
            (fb_posts_dir / "FBfanpage_MobileLegendsGameVN_260101_260531.csv").write_text(
                "ID,Platform,Channel id,Channel name,Channel tags,Category,Post id,Post type,Post description,Link,Publish time,Hashtag\n"
                "31896403,Facebook,1722990624697290,Mobile Legends: Bang Bang,,track-competitors,1017385504113773,VIDEO,\"Launch day update is live\",https://example.com/post,2026-01-01 08:00:00,#launch\n",
                encoding="utf-8",
            )

            stats = run_etl(
                db_path=db_path,
                config_path=Path("examples/config.json"),
                input_dir=input_dir,
            )

            self.assertEqual(stats.raw_fb_posts, 1)

            conn = open_connection(db_path)
            try:
                row = conn.execute(
                    """
                    SELECT
                        source_post_id, fb_page_id, channel_id, channel_name, post_type,
                        post_description, duration, link, publish_time, hashtag,
                        engagement, reaction, comment, share, view, source_file
                    FROM raw_fb_posts
                    """
                ).fetchone()

                self.assertEqual(row["source_post_id"], "1017385504113773")
                self.assertEqual(row["fb_page_id"], "1722990624697290")
                self.assertEqual(row["channel_id"], "1722990624697290")
                self.assertEqual(row["channel_name"], "Mobile Legends: Bang Bang")
                self.assertEqual(row["post_type"], "VIDEO")
                self.assertEqual(row["post_description"], "Launch day update is live")
                self.assertEqual(row["duration"], "")
                self.assertEqual(row["link"], "https://example.com/post")
                self.assertEqual(row["publish_time"], "2026-01-01 08:00:00")
                self.assertEqual(row["hashtag"], "#launch")
                self.assertEqual(row["engagement"], "")
                self.assertEqual(row["reaction"], "")
                self.assertEqual(row["comment"], "")
                self.assertEqual(row["share"], "")
                self.assertEqual(row["view"], "")
                self.assertEqual(row["source_file"], "fb_posts/FBfanpage_MobileLegendsGameVN_260101_260531.csv")
            finally:
                conn.close()
        finally:
            shutil.rmtree(temp_root, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
