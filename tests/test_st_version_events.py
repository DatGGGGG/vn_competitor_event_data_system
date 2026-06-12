from __future__ import annotations

import json
import shutil
import unittest
from pathlib import Path

from vn_event_dw.etl import init_db, open_connection
from vn_event_dw.st_version_events import load_st_version_events


class StVersionEventTests(unittest.TestCase):
    def test_load_st_version_events_creates_one_event_per_version_row(self) -> None:
        temp_root = Path.cwd() / "_tmp_st_version_event_load_test"
        db_path = temp_root / "warehouse.db"
        shutil.rmtree(temp_root, ignore_errors=True)
        temp_root.mkdir(parents=True, exist_ok=True)

        try:
            conn = open_connection(db_path)
            try:
                init_db(conn)
                conn.executemany(
                    """
                    INSERT INTO raw_st_version (
                        source_version_id, unified_app_id, os, app_id, country,
                        version_time, version_name, before_version, after_version,
                        version_summary, raw_payload, version_payload, source_file, ingested_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    [
                        (
                            "version-1",
                            "app-1",
                            "ios",
                            "12345",
                            "VN",
                            "2026-06-08T00:00:00Z",
                            "1.2.3",
                            "1.2.2",
                            "1.2.3",
                            "Patch release; New Hero: Aamon, improved effects",
                            json.dumps({"after": "1.2.3", "before": "1.2.2", "version_summary": "Patch release; New Hero: Aamon, improved effects"}, ensure_ascii=False),
                            json.dumps({"after": "1.2.3", "before": "1.2.2", "version_summary": "Patch release; New Hero: Aamon, improved effects"}, ensure_ascii=False),
                            "source.json",
                            "2026-06-08T01:00:00Z",
                        ),
                        (
                            "version-2",
                            "app-1",
                            "android",
                            "com.game.one",
                            "VN",
                            "2026-06-09T00:00:00Z",
                            "2.0.0",
                            "1.9.9",
                            "2.0.0",
                            None,
                            json.dumps({"after": "2.0.0", "before": "1.9.9"}, ensure_ascii=False),
                            json.dumps({"after": "2.0.0", "before": "1.9.9"}, ensure_ascii=False),
                            "source.json",
                            "2026-06-09T01:00:00Z",
                        ),
                        (
                            "ver_001",
                            "app-legacy",
                            "",
                            "",
                            "",
                            "2026-06-01T04:10:00+07:00",
                            "1.2.0",
                            None,
                            None,
                            "bugfix",
                            json.dumps({"notes": "bugfix"}, ensure_ascii=False),
                            json.dumps({"notes": "bugfix"}, ensure_ascii=False),
                            "st_version.csv",
                            "2026-06-10T01:00:00Z",
                        ),
                    ],
                )

                inserted = load_st_version_events(conn)
                self.assertEqual(inserted, 2)

                rows = conn.execute(
                    """
                    SELECT
                        st_version_event_id,
                        source_row_id,
                        unified_app_id,
                        event_name,
                        estimated_start_date,
                        estimated_end_date,
                        event_description,
                        source_refs
                    FROM st_version_events
                    ORDER BY source_row_id, event_name
                    """
                ).fetchall()

                self.assertEqual(len(rows), 2)
                first_row = next(row for row in rows if row["source_row_id"] == "version-1")
                self.assertTrue(first_row["st_version_event_id"].startswith("stver_"))
                self.assertEqual(first_row["event_name"], "ios Version Update 1.2.3")
                self.assertEqual(first_row["estimated_start_date"], "2026-06-08")
                self.assertEqual(first_row["estimated_end_date"], "2026-06-08")
                self.assertEqual(first_row["event_description"], "Patch release; New Hero: Aamon, improved effects")
                first_refs = json.loads(first_row["source_refs"])
                self.assertIsNone(first_refs[0]["source_detail"])

                second_row = next(row for row in rows if row["source_row_id"] == "version-2")
                self.assertEqual(second_row["event_name"], "android Version Update 2.0.0")
                self.assertEqual(second_row["estimated_start_date"], "2026-06-09")
                self.assertEqual(second_row["estimated_end_date"], "2026-06-09")
                self.assertIsNone(second_row["event_description"])
                self.assertFalse(any(row["source_row_id"] == "ver_001" for row in rows))
            finally:
                conn.close()
        finally:
            shutil.rmtree(temp_root, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
