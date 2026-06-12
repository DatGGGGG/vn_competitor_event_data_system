from __future__ import annotations

import json
import shutil
import unittest
from pathlib import Path

from vn_event_dw.etl import init_db, open_connection
from vn_event_dw.st_update_events import load_st_app_update_events


class StUpdateEventTests(unittest.TestCase):
    def test_init_db_drops_legacy_fact_tables_and_keeps_update_events_table(self) -> None:
        temp_root = Path.cwd() / "_tmp_st_update_schema_test"
        db_path = temp_root / "warehouse.db"
        shutil.rmtree(temp_root, ignore_errors=True)
        temp_root.mkdir(parents=True, exist_ok=True)

        try:
            conn = open_connection(db_path)
            try:
                conn.executescript(
                    """
                    CREATE TABLE fact_fb_posts (id TEXT PRIMARY KEY);
                    CREATE TABLE fact_deterministic_events (id TEXT PRIMARY KEY);
                    CREATE TABLE fact_rule_detected_events (id TEXT PRIMARY KEY);
                    """
                )
                conn.commit()

                init_db(conn)

                table_names = {
                    row["name"]
                    for row in conn.execute(
                        """
                        SELECT name
                        FROM sqlite_master
                        WHERE type = 'table'
                        """
                    ).fetchall()
                }

                self.assertNotIn("fact_fb_posts", table_names)
                self.assertNotIn("fact_deterministic_events", table_names)
                self.assertNotIn("fact_rule_detected_events", table_names)
                self.assertIn("st_app_update_events", table_names)
            finally:
                conn.close()
        finally:
            shutil.rmtree(temp_root, ignore_errors=True)

    def test_load_st_app_update_events_matches_reference_style_logic(self) -> None:
        temp_root = Path.cwd() / "_tmp_st_update_event_load_test"
        db_path = temp_root / "warehouse.db"
        shutil.rmtree(temp_root, ignore_errors=True)
        temp_root.mkdir(parents=True, exist_ok=True)

        try:
            conn = open_connection(db_path)
            try:
                init_db(conn)
                conn.executemany(
                    """
                    INSERT INTO raw_st_app_update (
                        source_update_id, unified_app_id, os, app_id, country,
                        update_time, update_type, name, subtitle, description_text,
                        events_json, events_raw, raw_payload, update_payload, source_file, ingested_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    [
                        (
                            "update-1",
                            "app-1",
                            "android",
                            "com.game.one",
                            "VN",
                            "2026-06-08T00:00:00Z",
                            "release",
                            "Update",
                            "Season 9 launch",
                            "Row description. Extra text.",
                            json.dumps(
                                [
                                    {
                                        "event_id": "evt-1",
                                        "name": "Update",
                                        "subtitle": "New Hero Release",
                                        "description": "Before description",
                                    },
                                    {
                                        "event_id": "evt-2",
                                        "name": "",
                                        "subtitle": "",
                                        "description": "First line. Second line.",
                                    },
                                ],
                                ensure_ascii=False,
                            ),
                            json.dumps(
                                {
                                    "before": [],
                                    "after": [
                                        {
                                            "event_id": "evt-1",
                                            "name": "Update",
                                            "subtitle": "New Hero Release",
                                            "description": "Before description",
                                        },
                                        {
                                            "event_id": "evt-2",
                                            "name": "",
                                            "subtitle": "",
                                            "description": "First line. Second line.",
                                        },
                                    ],
                                },
                                ensure_ascii=False,
                            ),
                            json.dumps(
                                {
                                    "name": "Update",
                                    "subtitle": "Season 9 launch",
                                    "description": "Row description. Extra text.",
                                },
                                ensure_ascii=False,
                            ),
                            json.dumps({"legacy": True}, ensure_ascii=False),
                            "source.csv",
                            "2026-06-08T01:00:00Z",
                        ),
                        (
                            "update-2",
                            "app-1",
                            "ios",
                            "12345",
                            "VN",
                            "2026-06-09T00:00:00Z",
                            "release",
                            "",
                            "",
                            json.dumps({"after": "Season 37 launch. More text.", "before": "Old season text."}, ensure_ascii=False),
                            None,
                            None,
                            json.dumps({"legacy": True}, ensure_ascii=False),
                            json.dumps({"legacy": True}, ensure_ascii=False),
                            "source.csv",
                            "2026-06-09T01:00:00Z",
                        ),
                        (
                            "update-3",
                            "app-1",
                            "ios",
                            "12345",
                            "VN",
                            "2026-06-10T00:00:00Z",
                            "release",
                            "",
                            "",
                            "Duplicate source row",
                            None,
                            json.dumps(
                                {
                                    "before": [
                                        {
                                            "event_id": "evt-1",
                                            "name": "Another name for duplicate id",
                                            "description": "Duplicate item should be ignored",
                                        }
                                    ],
                                    "after": [],
                                },
                                ensure_ascii=False,
                            ),
                            json.dumps({"legacy": True}, ensure_ascii=False),
                            json.dumps({"legacy": True}, ensure_ascii=False),
                            "source.csv",
                            "2026-06-10T01:00:00Z",
                        ),
                    ],
                )

                inserted = load_st_app_update_events(conn)
                self.assertEqual(inserted, 2)

                rows = conn.execute(
                    """
                    SELECT
                        st_update_event_id,
                        event_id,
                        source_row_id,
                        unified_app_id,
                        event_name,
                        estimated_start_date,
                        estimated_end_date,
                        event_description,
                        source_refs
                    FROM st_app_update_events
                    ORDER BY source_row_id, estimated_start_date, event_name
                    """
                ).fetchall()

                self.assertEqual(len(rows), 2)
                hero_row = next(row for row in rows if row["event_name"] == "New Hero Release")
                self.assertEqual(hero_row["source_row_id"], "update-1")
                self.assertEqual(hero_row["event_id"], "evt-1")
                self.assertEqual(hero_row["st_update_event_id"], "stupd_evt-1")
                self.assertEqual(hero_row["estimated_start_date"], "2026-06-08")
                self.assertEqual(hero_row["estimated_end_date"], "2026-06-08")
                self.assertIn("Before description", hero_row["event_description"])

                source_refs = json.loads(hero_row["source_refs"])
                self.assertEqual(source_refs[0]["source_table"], "raw_st_app_update")
                self.assertEqual(source_refs[0]["source_row_id"], "update-1")
                self.assertEqual(source_refs[0]["source_detail"], "evt-1")

                deduped_rows = [row for row in rows if row["source_row_id"] == "update-1"]
                self.assertEqual(len(deduped_rows), 2)
                self.assertTrue(any(row["event_name"] == "First line" for row in deduped_rows))
                self.assertFalse(any(row["source_row_id"] == "update-2" for row in rows))
                self.assertFalse(any(row["source_row_id"] == "update-3" for row in rows))
            finally:
                conn.close()
        finally:
            shutil.rmtree(temp_root, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
