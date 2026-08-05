from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from vn_event_dw.admin_config import (
    AdminConfigError,
    AdminGameInput,
    add_game_to_payload,
    build_game_preview,
    load_config_payload,
    validate_payload_has_required_targets,
    write_config_payload_atomic,
)


class AdminConfigTests(unittest.TestCase):
    def _payload(self) -> dict[str, object]:
        return {
            "rule_keywords": ["release"],
            "app_mappings": [
                {
                    "unified_app_id": "existing_app",
                    "fb_page_id": "existing_page",
                    "app_name": "Existing Game",
                    "is_active": True,
                }
            ],
            "sensortower_targets": [
                {
                    "unified_app_id": "existing_app",
                    "os": "android",
                    "app_id": "com.existing.game",
                    "country": "VN",
                },
                {
                    "unified_app_id": "existing_app",
                    "os": "ios",
                    "app_id": "123456789",
                    "country": "VN",
                },
            ],
        }

    def test_add_game_updates_mapping_and_sensortower_targets(self) -> None:
        payload = self._payload()
        preview = build_game_preview(
            payload,
            AdminGameInput(
                app_name="New Game",
                fb_page_id="new_page",
                android_app_id="com.new.game",
                ios_app_id="987654321",
            ),
        )

        updated = add_game_to_payload(payload, preview)

        self.assertEqual(updated["app_mappings"][0]["app_name"], "New Game")
        self.assertEqual(updated["sensortower_targets"][0]["os"], "android")
        self.assertEqual(updated["sensortower_targets"][1]["os"], "ios")
        validate_payload_has_required_targets(updated, preview.unified_app_id)

    def test_duplicate_fb_page_id_is_rejected(self) -> None:
        with self.assertRaisesRegex(AdminConfigError, "Facebook page ID already exists"):
            build_game_preview(
                self._payload(),
                AdminGameInput(
                    app_name="New Game",
                    fb_page_id="existing_page",
                    android_app_id="com.new.game",
                    ios_app_id="987654321",
                ),
            )

    def test_duplicate_sensortower_target_is_rejected(self) -> None:
        with self.assertRaisesRegex(AdminConfigError, "SensorTower target already exists"):
            build_game_preview(
                self._payload(),
                AdminGameInput(
                    app_name="New Game",
                    fb_page_id="new_page",
                    android_app_id="com.existing.game",
                    ios_app_id="987654321",
                ),
            )

    def test_missing_ios_target_is_rejected(self) -> None:
        payload = self._payload()
        preview = build_game_preview(
            payload,
            AdminGameInput(
                app_name="New Game",
                fb_page_id="new_page",
                android_app_id="com.new.game",
                ios_app_id="987654321",
            ),
        )
        updated = add_game_to_payload(payload, preview)
        updated["sensortower_targets"] = [
            item
            for item in updated["sensortower_targets"]
            if not (item["unified_app_id"] == preview.unified_app_id and item["os"] == "ios")
        ]

        with self.assertRaisesRegex(AdminConfigError, "Missing SensorTower targets"):
            validate_payload_has_required_targets(updated, preview.unified_app_id)

    def test_generated_unified_app_id_is_stable_and_unique(self) -> None:
        payload = self._payload()
        first = build_game_preview(
            payload,
            AdminGameInput(
                app_name="New Game",
                fb_page_id="new_page",
                android_app_id="com.new.game",
                ios_app_id="987654321",
            ),
        )
        second = build_game_preview(
            payload,
            AdminGameInput(
                app_name="New Game",
                fb_page_id="new_page_2",
                android_app_id="com.new.game2",
                ios_app_id="987654322",
            ),
        )

        self.assertEqual(len(first.unified_app_id), 24)
        self.assertNotEqual(first.unified_app_id, second.unified_app_id)

    def test_write_config_payload_atomic_keeps_valid_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "config.json"
            write_config_payload_atomic(path, self._payload())

            loaded = load_config_payload(path)

        self.assertEqual(loaded["rule_keywords"], ["release"])
        self.assertEqual(loaded["app_mappings"][0]["app_name"], "Existing Game")


if __name__ == "__main__":
    unittest.main()
