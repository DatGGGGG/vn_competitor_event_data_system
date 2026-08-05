from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from vn_event_dw.api import create_app
from vn_event_dw.etl import init_db, open_connection


class AdminApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.db_path = self.root / "warehouse.db"
        self.config_path = self.root / "config.json"
        self.job_dir = self.root / "admin_jobs"
        self.config_path.write_text(
            json.dumps(
                {
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
            )
            + "\n",
            encoding="utf-8",
        )
        conn = open_connection(self.db_path)
        try:
            init_db(conn)
            conn.commit()
        finally:
            conn.close()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _client(self) -> TestClient:
        env = {
            "ADMIN_UI_ENABLED": "1",
            "ADMIN_PASSWORD": "secret",
            "ADMIN_CONFIG_PATH": str(self.config_path),
            "ADMIN_JOB_DIR": str(self.job_dir),
            "ADMIN_GIT_ENABLED": "0",
        }
        patcher = patch.dict(os.environ, env, clear=False)
        patcher.start()
        self.addCleanup(patcher.stop)
        return TestClient(create_app(db_path=self.db_path))

    def test_admin_games_rejects_unauthenticated_request(self) -> None:
        client = self._client()

        response = client.get("/admin/games")

        self.assertEqual(response.status_code, 401)

    def test_correct_password_allows_access(self) -> None:
        client = self._client()

        login = client.post("/admin/login", data="password=secret", headers={"content-type": "application/x-www-form-urlencoded"})
        response = client.get("/admin/games")

        self.assertEqual(login.status_code, 200)
        self.assertEqual(response.status_code, 200)
        self.assertIn("Existing Game", response.text)

    def test_add_game_preview_returns_expected_diff(self) -> None:
        client = self._client()
        client.post("/admin/login", data="password=secret", headers={"content-type": "application/x-www-form-urlencoded"})

        response = client.post(
            "/admin/games/preview",
            data=(
                "app_name=Preview+Game&"
                "fb_page_id=preview_page&"
                "android_app_id=com.preview.game&"
                "ios_app_id=987654321&"
                "country=VN"
            ),
            headers={"content-type": "application/x-www-form-urlencoded"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn("Preview Game", response.text)
        self.assertIn("com.preview.game", response.text)
        self.assertIn("987654321", response.text)


if __name__ == "__main__":
    unittest.main()
