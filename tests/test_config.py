from __future__ import annotations

import unittest
from pathlib import Path

from vn_event_dw.config import load_pipeline_config


class ConfigTests(unittest.TestCase):
    def test_load_pipeline_config_parses_sensor_tower_targets(self) -> None:
        config = load_pipeline_config(Path("examples/config.json"))

        self.assertGreaterEqual(len(config.app_mappings), 5)
        self.assertGreaterEqual(len(config.sensortower_targets), 2)
        self.assertEqual(config.sensortower_targets[0].unified_app_id, "57955d280211a6718a000002")
        self.assertEqual(config.rule_keywords, ("release", "launch", "update"))


if __name__ == "__main__":
    unittest.main()
