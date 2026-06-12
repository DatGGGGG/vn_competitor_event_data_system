from __future__ import annotations

import json
import shutil
import unittest
from datetime import date
from pathlib import Path

from vn_event_dw.config import load_pipeline_config
from vn_event_dw.etl import init_db, open_connection
from vn_event_dw.sensortower_raw import (
    RawExtractionWindow,
    extract_sensortower_raw,
    load_sensortower_raw_manifest,
    resolve_raw_window,
    resolve_tracked_targets,
)


class FakeSensorTowerClient:
    def __init__(self) -> None:
        self.update_calls: list[dict[str, object]] = []
        self.version_calls: list[dict[str, object]] = []

    def get_app_update_history(self, *, os_name: str, app_id: str, country: str, date_limit: int) -> dict[str, object]:
        self.update_calls.append(
            {
                "os_name": os_name,
                "app_id": app_id,
                "country": country,
                "date_limit": date_limit,
            }
        )
        return {
            "app_info": {"unified_app_id": "57955d280211a6718a000002"},
            "update_data": [
                [
                    "2026-04-16T00:00:00Z",
                    {
                        "version": {"before": "1.0", "after": "1.1"},
                        "description": {"after": "Update note after", "before": "Update note before", "diff": "<b>diff</b>"},
                        "events": {
                            "after": [
                                {
                                    "event_id": "evt-1",
                                    "name": "Update",
                                    "subtitle": "Season Launch",
                                    "description": "Update note after",
                                    "event_start_date": "2026-04-16T00:00:00Z",
                                    "event_end_date": "2026-04-20T00:00:00Z",
                                }
                            ],
                            "before": [],
                        },
                        "featured_user_feedback": {"after": [{"title": "Good update"}]},
                        "content_rating": {"after": "12+"},
                        "channel": "ios",
                        "notes": "season launch",
                        "name": "Update note",
                        "update_type": "release",
                    },
                ],
                [
                    "2026-03-01T00:00:00Z",
                    {
                        "version": {"before": "0.9", "after": "1.0"},
                        "name": "Older update",
                        "update_type": "release",
                    },
                ],
            ],
        }

    def get_app_version_history(self, *, os_name: str, app_id: str, country: str) -> dict[str, object]:
        self.version_calls.append(
            {
                "os_name": os_name,
                "app_id": app_id,
                "country": country,
            }
        )
        return {
            "app_info": {"unified_app_id": "57955d280211a6718a000002"},
            "update_data": {
                "2026-04-20T00:00:00Z": {
                    "before": "1.0",
                    "after": "1.1",
                    "version_summary": "Patch release",
                },
                "2026-03-01T00:00:00Z": {
                    "before": "0.9",
                    "after": "1.0",
                    "version_summary": "Older release",
                },
            },
        }


class SensorTowerRawTests(unittest.TestCase):
    def test_resolve_raw_window_uses_lookback_days(self) -> None:
        window = resolve_raw_window(
            since=None,
            until=None,
            lookback_days=3,
            today=date(2026, 4, 17),
        )
        self.assertEqual(window.since, date(2026, 4, 14))
        self.assertEqual(window.lookback_days, 3)

    def test_extract_and_load_raw_manifest(self) -> None:
        config = load_pipeline_config(Path("examples/config.json"))
        target = resolve_tracked_targets(config, ["57955d280211a6718a000002"])[0]
        client = FakeSensorTowerClient()

        output_dir = Path.cwd() / "_tmp_sensortower_raw_test"
        db_path = output_dir / "warehouse.db"
        if output_dir.exists():
            shutil.rmtree(output_dir, ignore_errors=True)
        output_dir.mkdir(parents=True, exist_ok=True)

        try:
            summary = extract_sensortower_raw(
                client=client,  # type: ignore[arg-type]
                targets=[target],
                window=RawExtractionWindow(since=date(2026, 4, 1), until=date(2026, 4, 30), lookback_days=None),
                output_dir=output_dir,
            )

            manifest_path = Path(summary.manifest_path)
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(summary.snapshot_count, 2)
            self.assertIsNone(manifest["loaded_at"])
            self.assertEqual(manifest["window"]["since"], "2026-04-01")
            self.assertEqual(len(manifest["snapshots"]), 2)
            self.assertGreaterEqual(int(client.update_calls[0]["date_limit"]), 1)

            conn = open_connection(db_path)
            try:
                init_db(conn)
                load_summary = load_sensortower_raw_manifest(conn, manifest_path=manifest_path)
                self.assertEqual(load_summary.loaded_snapshots, 2)
                self.assertEqual(load_summary.skipped_snapshots, 0)
                self.assertEqual(load_summary.update_rows, 1)
                self.assertEqual(load_summary.version_rows, 1)
                self.assertEqual(load_summary.st_update_event_rows, 1)
                self.assertEqual(load_summary.st_version_event_rows, 1)

                counts = {
                    "raw_st_app_update": conn.execute("SELECT COUNT(*) FROM raw_st_app_update").fetchone()[0],
                    "raw_st_version": conn.execute("SELECT COUNT(*) FROM raw_st_version").fetchone()[0],
                    "st_app_update_events": conn.execute("SELECT COUNT(*) FROM st_app_update_events").fetchone()[0],
                    "st_version_events": conn.execute("SELECT COUNT(*) FROM st_version_events").fetchone()[0],
                }
                self.assertEqual(counts["raw_st_app_update"], 1)
                self.assertEqual(counts["raw_st_version"], 1)
                self.assertEqual(counts["st_app_update_events"], 1)
                self.assertEqual(counts["st_version_events"], 1)

                raw_update = conn.execute(
                    """
                    SELECT
                        os, app_id, country, name, description_text,
                        description_before_text, description_after_text,
                        description_diff_html, version_before, version_after,
                        featured_user_feedback_raw, content_rating_raw,
                        events_json, events_raw,
                        channel_raw, notes_raw, raw_payload
                    FROM raw_st_app_update
                    """
                ).fetchone()
                self.assertEqual(raw_update["os"], target.os)
                self.assertEqual(raw_update["app_id"], target.app_id)
                self.assertEqual(raw_update["country"], target.country)
                self.assertEqual(raw_update["name"], "Update note")
                self.assertEqual(raw_update["description_text"], "Update note after")
                self.assertEqual(raw_update["description_before_text"], "Update note before")
                self.assertEqual(raw_update["description_after_text"], "Update note after")
                self.assertEqual(raw_update["description_diff_html"], "<b>diff</b>")
                self.assertEqual(raw_update["version_before"], "1.0")
                self.assertEqual(raw_update["version_after"], "1.1")
                self.assertIn("Good update", raw_update["featured_user_feedback_raw"])
                self.assertIn("12+", raw_update["content_rating_raw"])
                self.assertIn("Season Launch", raw_update["events_json"])
                self.assertIn("\"after\"", raw_update["events_raw"])
                self.assertEqual(raw_update["channel_raw"], "ios")
                self.assertEqual(raw_update["notes_raw"], "season launch")
                self.assertIn("featured_user_feedback", raw_update["raw_payload"])
                self.assertIn("\"events\"", raw_update["raw_payload"])

                raw_version = conn.execute(
                    """
                    SELECT
                        os, app_id, country, version_name,
                        before_version, after_version, version_summary,
                        raw_payload, version_payload
                    FROM raw_st_version
                    """
                ).fetchone()
                self.assertEqual(raw_version["os"], target.os)
                self.assertEqual(raw_version["app_id"], target.app_id)
                self.assertEqual(raw_version["country"], target.country)
                self.assertEqual(raw_version["version_name"], "1.1")
                self.assertEqual(raw_version["before_version"], "1.0")
                self.assertEqual(raw_version["after_version"], "1.1")
                self.assertEqual(raw_version["version_summary"], "Patch release")
                self.assertIn("\"after\": \"1.1\"", raw_version["raw_payload"])
                self.assertEqual(raw_version["raw_payload"], raw_version["version_payload"])

                built_event = conn.execute(
                    """
                    SELECT st_update_event_id, event_id, event_name
                    FROM st_app_update_events
                    """
                ).fetchone()
                self.assertEqual(built_event["event_id"], "evt-1")
                self.assertEqual(built_event["st_update_event_id"], "stupd_evt-1")
                self.assertEqual(built_event["event_name"], "Season Launch")

                version_event = conn.execute(
                    """
                    SELECT st_version_event_id, event_name, event_description
                    FROM st_version_events
                    """
                ).fetchone()
                self.assertTrue(version_event["st_version_event_id"].startswith("stver_"))
                self.assertEqual(version_event["event_name"], "ios Version Update 1.1")
                self.assertEqual(version_event["event_description"], "Patch release")

                skipped = load_sensortower_raw_manifest(conn, manifest_path=manifest_path)
                self.assertEqual(skipped.loaded_snapshots, 2)
                self.assertEqual(skipped.skipped_snapshots, 0)
                self.assertEqual(
                    conn.execute("SELECT COUNT(*) FROM raw_st_app_update").fetchone()[0],
                    1,
                )
            finally:
                conn.close()
        finally:
            shutil.rmtree(output_dir, ignore_errors=True)

    def test_load_manifest_skips_missing_snapshot_files(self) -> None:
        config = load_pipeline_config(Path("examples/config.json"))
        target = resolve_tracked_targets(config, ["57955d280211a6718a000002"])[0]
        client = FakeSensorTowerClient()

        output_dir = Path.cwd() / "_tmp_sensortower_raw_missing_snapshot"
        db_path = output_dir / "warehouse.db"
        if output_dir.exists():
            shutil.rmtree(output_dir, ignore_errors=True)
        output_dir.mkdir(parents=True, exist_ok=True)

        try:
            summary = extract_sensortower_raw(
                client=client,  # type: ignore[arg-type]
                targets=[target],
                window=RawExtractionWindow(since=date(2026, 4, 1), until=date(2026, 4, 30), lookback_days=None),
                output_dir=output_dir,
            )
            manifest_path = Path(summary.manifest_path)
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            missing_rel_path = manifest["snapshots"][1]["path"]
            missing_path = manifest_path.parent / missing_rel_path
            missing_path.unlink()

            conn = open_connection(db_path)
            try:
                init_db(conn)
                load_summary = load_sensortower_raw_manifest(conn, manifest_path=manifest_path)
                self.assertEqual(load_summary.loaded_snapshots, 1)
                self.assertEqual(load_summary.skipped_snapshots, 1)
                self.assertEqual(load_summary.update_rows, 1)
                self.assertEqual(load_summary.version_rows, 0)
                self.assertEqual(load_summary.st_update_event_rows, 1)
                self.assertEqual(load_summary.st_version_event_rows, 0)

                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                self.assertEqual(manifest["loaded_summary"]["loaded_snapshots"], 1)
                self.assertEqual(manifest["loaded_summary"]["skipped_snapshots"], 1)
                self.assertEqual(manifest["loaded_summary"]["skipped_paths"], [missing_rel_path])
            finally:
                conn.close()
        finally:
            shutil.rmtree(output_dir, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
