from __future__ import annotations

import unittest

from vn_event_dw.fb_event_ai import _normalize_responses_base_url


class FbEventAiTests(unittest.TestCase):
    def test_normalize_responses_base_url_accepts_root_path(self) -> None:
        self.assertEqual(
            _normalize_responses_base_url("https://compass.llm.shopee.io/compass-api/v1"),
            "https://compass.llm.shopee.io/compass-api/v1",
        )

    def test_normalize_responses_base_url_strips_responses_suffix(self) -> None:
        self.assertEqual(
            _normalize_responses_base_url("https://compass.llm.shopee.io/compass-api/v1/responses"),
            "https://compass.llm.shopee.io/compass-api/v1",
        )


if __name__ == "__main__":
    unittest.main()
