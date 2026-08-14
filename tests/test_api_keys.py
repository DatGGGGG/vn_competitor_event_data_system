from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from vn_event_dw.api_keys import add_api_key, key_matches_store, list_api_keys, revoke_api_key


class ApiKeyStoreTests(unittest.TestCase):
    def test_generate_list_and_revoke_api_key(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            keys_file = Path(tmp_dir) / "api_keys.json"

            generated = add_api_key(keys_file, name="Ryan")

            self.assertTrue(generated.key.startswith("vnedw_"))
            self.assertTrue(key_matches_store(keys_file, api_key=generated.key))
            self.assertEqual(
                list_api_keys(keys_file),
                [
                    {
                        "name": "Ryan",
                        "key_prefix": generated.key_prefix,
                        "created_at": generated.created_at,
                        "revoked_at": None,
                        "active": True,
                    }
                ],
            )

            revoke_result = revoke_api_key(keys_file, name="Ryan")

            self.assertEqual(revoke_result["name"], "Ryan")
            self.assertFalse(key_matches_store(keys_file, api_key=generated.key))
            self.assertFalse(list_api_keys(keys_file)[0]["active"])

    def test_raw_key_is_not_stored(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            keys_file = Path(tmp_dir) / "api_keys.json"

            generated = add_api_key(keys_file, name="teammate")
            stored_text = keys_file.read_text(encoding="utf-8")
            payload = json.loads(stored_text)

            self.assertNotIn(generated.key, stored_text)
            self.assertTrue(payload["keys"][0]["key_hash"].startswith("sha256:"))

    def test_duplicate_active_name_is_rejected(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            keys_file = Path(tmp_dir) / "api_keys.json"
            add_api_key(keys_file, name="same-user")

            with self.assertRaisesRegex(ValueError, "already exists"):
                add_api_key(keys_file, name="same-user")


if __name__ == "__main__":
    unittest.main()
