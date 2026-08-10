from __future__ import annotations

import json
import shutil
import unittest
from datetime import datetime, timezone
from pathlib import Path

from vn_event_dw.etl import init_db, load_config, open_connection
from vn_event_dw.socialdata import SocialDataApp, SocialDataChannel, SocialDataPost
from vn_event_dw.socialdata_sync import diagnose_socialdata_game_into_connection, sync_socialdata_posts_into_connection


def _post(
    *,
    post_id: int,
    source_post_id: str,
    created_at: str,
    name: str = "PUBG MOBILE post",
) -> SocialDataPost:
    return SocialDataPost(
        id=post_id,
        channel_id=13455,
        sub=source_post_id,
        alias=None,
        type=6,
        name=name,
        url=f"https://www.facebook.com/PUBGMobileVN/posts/{source_post_id}",
        tags="#PUBGMOBILEVN",
        created_at=created_at,
        thumbnail=None,
        metrics=None,
    )


class _FakeSocialDataClient:
    def __init__(self) -> None:
        self.list_posts_calls: list[tuple[int, int]] = []
        self.get_post_calls: list[int] = []
        self.page_posts: dict[int, tuple[list[SocialDataPost], int]] = {
            0: (
                [
                    _post(
                        post_id=43038939,
                        source_post_id="1196807109095681",
                        created_at="2026-06-15T15:00:54.000Z",
                        name="Recent PUBG MOBILE post",
                    ),
                    _post(
                        post_id=43038940,
                        source_post_id="too_old",
                        created_at="2026-05-01T00:00:00.000Z",
                        name="Older post",
                    ),
                ],
                2,
            )
        }

    def iter_channels(self, *, app_id: int, per_page: int = 100) -> list[SocialDataChannel]:
        self.last_iter_channels = (app_id, per_page)
        return [
            SocialDataChannel(
                id=13455,
                plat=1,
                sub="251269595573816",
                alias="100050168123213",
                name="PUBG Mobile VN",
                url="https://www.facebook.com/PUBGMobileVN",
                status=1,
                created_at="2025-02-03T11:39:28.137Z",
                tags=None,
                metrics=None,
            ),
            SocialDataChannel(
                id=99999,
                plat=1,
                sub="not_in_config",
                alias=None,
                name="Ignore Me",
                url=None,
                status=1,
                created_at="2025-01-01T00:00:00.000Z",
                tags=None,
                metrics=None,
            ),
        ]

    def list_posts(
        self,
        *,
        app_id: int,
        page: int = 0,
        per_page: int = 100,
        sort_field: str | None = None,
        sort_order: str = "ASC",
        filter: dict[str, object] | None = None,
    ) -> tuple[list[SocialDataPost], int]:
        self.list_posts_calls.append((app_id, page))
        return self.page_posts.get(page, ([], 2))

    def get_post(
        self,
        *,
        app_id: int,
        post_id: int,
        with_metrics: bool = True,
        metric_duration: int | None = None,
    ) -> SocialDataPost:
        self.get_post_calls.append(post_id)
        for posts, _total in self.page_posts.values():
            for post in posts:
                if post.id == post_id:
                    return SocialDataPost(
                        id=post.id,
                        channel_id=post.channel_id,
                        sub=post.sub,
                        alias=post.alias,
                        type=post.type,
                        name=post.name,
                        url=post.url,
                        tags=post.tags,
                        created_at=post.created_at,
                        thumbnail=post.thumbnail,
                        metrics={
                            "m0": 840,
                            "m1": 699,
                            "m2": 112,
                            "m3": 29,
                            "m4": 96128,
                            "m61": 7,
                        },
                    )
        raise AssertionError(f"Unexpected post_id={post_id}")


class SocialDataSyncTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_root = Path.cwd() / "_tmp_socialdata_sync_test"
        self.db_path = self.temp_root / "warehouse.db"
        self.config_path = self.temp_root / "config.json"
        shutil.rmtree(self.temp_root, ignore_errors=True)
        self.temp_root.mkdir(parents=True, exist_ok=True)
        self.config_path.write_text(
            json.dumps(
                {
                    "rule_keywords": [],
                    "app_mappings": [
                        {
                            "unified_app_id": "5aad42d99f479f0a74d40440",
                            "fb_page_id": "251269595573816",
                            "app_name": "PUBG MOBILE",
                            "is_active": True,
                        }
                    ],
                    "sensortower_targets": [],
                }
            ),
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        shutil.rmtree(self.temp_root, ignore_errors=True)

    def test_sync_socialdata_posts_into_connection_upserts_recent_posts(self) -> None:
        conn = open_connection(self.db_path)
        client = _FakeSocialDataClient()
        try:
            init_db(conn)
            config = load_config(conn, self.config_path)
            stats = sync_socialdata_posts_into_connection(
                conn,
                config_app_mappings=config.app_mappings,
                client=client,
                app=SocialDataApp(id=80, slug="srcvn", name="[VN] New Game Source"),
                cutoff=datetime(2026, 6, 1, tzinfo=timezone.utc),
                progress=None,
            )

            self.assertEqual(stats.app_slug, "srcvn")
            self.assertEqual(stats.app_id, 80)
            self.assertEqual(stats.matched_channels, 1)
            self.assertEqual(stats.listed_posts, 1)
            self.assertEqual(stats.upserted_posts, 1)
            self.assertEqual(len(stats.channel_stats), 1)
            self.assertFalse(stats.channel_stats[0].stopped_on_cutoff)
            self.assertEqual(client.list_posts_calls, [(80, 0)])
            self.assertEqual(client.get_post_calls, [43038939])

            row = conn.execute(
                """
                SELECT
                    source_post_id, unified_app_id, fb_page_id, channel_id, channel_name, post_type,
                    post_description, duration, link, publish_time, hashtag,
                    engagement, reaction, comment, share, view, source_file
                FROM raw_fb_posts
                """
            ).fetchone()
            self.assertEqual(row["source_post_id"], "1196807109095681")
            self.assertEqual(row["unified_app_id"], "5aad42d99f479f0a74d40440")
            self.assertEqual(row["fb_page_id"], "251269595573816")
            self.assertEqual(row["channel_id"], "13455")
            self.assertEqual(row["channel_name"], "PUBG Mobile VN")
            self.assertEqual(row["post_type"], "VIDEO")
            self.assertEqual(row["post_description"], "Recent PUBG MOBILE post")
            self.assertEqual(row["duration"], "7")
            self.assertEqual(row["link"], "https://www.facebook.com/PUBGMobileVN/posts/1196807109095681")
            self.assertEqual(row["publish_time"], "2026-06-15T15:00:54.000Z")
            self.assertEqual(row["hashtag"], "#PUBGMOBILEVN")
            self.assertEqual(row["engagement"], "840")
            self.assertEqual(row["reaction"], "699")
            self.assertEqual(row["comment"], "112")
            self.assertEqual(row["share"], "29")
            self.assertEqual(row["view"], "96128")
            self.assertEqual(row["source_file"], "socialdata/srcvn/channel_13455.json")
        finally:
            conn.close()

    def test_sync_does_not_stop_when_page_mixes_recent_and_old_posts(self) -> None:
        conn = open_connection(self.db_path)
        client = _FakeSocialDataClient()
        client.page_posts[0] = (client.page_posts[0][0], 3)
        client.page_posts[1] = (
            [
                _post(
                    post_id=43038941,
                    source_post_id="1196807109095682",
                    created_at="2026-06-14T00:00:00.000Z",
                    name="Later recent post",
                )
            ],
            3,
        )
        try:
            init_db(conn)
            config = load_config(conn, self.config_path)
            stats = sync_socialdata_posts_into_connection(
                conn,
                config_app_mappings=config.app_mappings,
                client=client,
                app=SocialDataApp(id=80, slug="srcvn", name="[VN] New Game Source"),
                cutoff=datetime(2026, 6, 1, tzinfo=timezone.utc),
                per_page=2,
                progress=None,
            )

            self.assertEqual(stats.listed_posts, 2)
            self.assertEqual(stats.upserted_posts, 2)
            self.assertFalse(stats.channel_stats[0].stopped_on_cutoff)
            self.assertEqual(client.list_posts_calls, [(80, 0), (80, 1)])
            self.assertEqual(client.get_post_calls, [43038939, 43038941])
        finally:
            conn.close()

    def test_sync_stops_when_whole_page_is_older_than_cutoff(self) -> None:
        conn = open_connection(self.db_path)
        client = _FakeSocialDataClient()
        client.page_posts[0] = (
            [
                _post(
                    post_id=43038940,
                    source_post_id="too_old",
                    created_at="2026-05-01T00:00:00.000Z",
                    name="Older post",
                )
            ],
            2,
        )
        client.page_posts[1] = (
            [
                _post(
                    post_id=43038941,
                    source_post_id="should_not_fetch",
                    created_at="2026-06-14T00:00:00.000Z",
                )
            ],
            2,
        )
        try:
            init_db(conn)
            config = load_config(conn, self.config_path)
            stats = sync_socialdata_posts_into_connection(
                conn,
                config_app_mappings=config.app_mappings,
                client=client,
                app=SocialDataApp(id=80, slug="srcvn", name="[VN] New Game Source"),
                cutoff=datetime(2026, 6, 1, tzinfo=timezone.utc),
                per_page=1,
                progress=None,
            )

            self.assertEqual(stats.listed_posts, 0)
            self.assertEqual(stats.upserted_posts, 0)
            self.assertTrue(stats.channel_stats[0].stopped_on_cutoff)
            self.assertEqual(client.list_posts_calls, [(80, 0)])
            self.assertEqual(client.get_post_calls, [])
        finally:
            conn.close()

    def test_diagnostic_reports_socialdata_posts_missing_from_db(self) -> None:
        conn = open_connection(self.db_path)
        client = _FakeSocialDataClient()
        try:
            init_db(conn)
            config = load_config(conn, self.config_path)
            diagnostic = diagnose_socialdata_game_into_connection(
                conn,
                config_app_mappings=config.app_mappings,
                client=client,
                app=SocialDataApp(id=80, slug="srcvn", name="[VN] New Game Source"),
                unified_app_id="5aad42d99f479f0a74d40440",
                cutoff=datetime(2026, 6, 1, tzinfo=timezone.utc),
                limit=5,
            )

            self.assertFalse(diagnostic["ok"])
            self.assertEqual(diagnostic["matched_channels"], 1)
            self.assertEqual(diagnostic["missing_source_post_ids"], ["1196807109095681"])
            self.assertEqual(diagnostic["socialdata_latest_created_at"], "2026-06-15T15:00:54.000Z")
            self.assertIsNone(diagnostic["db_latest_publish_time"])
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main()
