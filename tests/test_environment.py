from __future__ import annotations

import os
import shutil
import unittest
from pathlib import Path

from vn_event_dw.environment import load_dotenv_file


class EnvironmentTests(unittest.TestCase):
    def test_load_dotenv_file_sets_missing_values(self) -> None:
        original_value = os.environ.get("SENSOR_TOWER_AUTH_TOKEN")
        temp_dir = Path.cwd() / "_tmp_env_test"
        try:
            os.environ.pop("SENSOR_TOWER_AUTH_TOKEN", None)
            shutil.rmtree(temp_dir, ignore_errors=True)
            temp_dir.mkdir(parents=True, exist_ok=True)
            env_path = temp_dir / ".env"
            env_path.write_text(
                "SENSOR_TOWER_AUTH_TOKEN=test-token\n"
                'SENSOR_TOWER_BASE_URL="https://example.test"\n',
                encoding="utf-8",
            )

            loaded = load_dotenv_file(env_path)
            self.assertTrue(loaded)
            self.assertEqual(os.environ["SENSOR_TOWER_AUTH_TOKEN"], "test-token")
            self.assertEqual(os.environ["SENSOR_TOWER_BASE_URL"], "https://example.test")
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)
            if original_value is None:
                os.environ.pop("SENSOR_TOWER_AUTH_TOKEN", None)
            else:
                os.environ["SENSOR_TOWER_AUTH_TOKEN"] = original_value


if __name__ == "__main__":
    unittest.main()
