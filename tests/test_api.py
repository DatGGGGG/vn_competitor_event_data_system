from __future__ import annotations

import unittest
from pathlib import Path
import uuid

from fastapi.testclient import TestClient

from vn_event_dw.api import create_app
from vn_event_dw.etl import init_db, open_connection


ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"


class ApiTests(unittest.TestCase):
    def setUp(self) -> None:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        self.db_path = DATA_DIR / f"test_api_{uuid.uuid4().hex}.db"
        if self.db_path.exists():
            self.db_path.unlink()
        conn = open_connection(self.db_path)
        try:
            init_db(conn)
            self._seed_data(conn)
            conn.commit()
        finally:
            conn.close()
        self.client = TestClient(create_app(db_path=self.db_path))

    def tearDown(self) -> None:
        if self.db_path.exists():
            self.db_path.unlink()

    def _seed_data(self, conn) -> None:
        conn.execute(
            """
            INSERT INTO config_app_mapping (unified_app_id, fb_page_id, app_name, is_active)
            VALUES
                ('app_a', 'page_a', 'Game A', 1),
                ('app_b', 'page_b', 'Game B', 1),
                ('mlbb_app', 'page_mlbb', 'Mobile Legends: Bang Bang', 1)
            """
        )
        conn.execute(
            """
            INSERT INTO unified_events (
                unified_event_id, unified_app_id, month_bucket, canonical_event_name, event_category,
                estimated_start_date, estimated_end_date, canonical_event_description,
                anchor_source_type, merge_confidence, merge_model, prompt_version, created_at, updated_at
            )
            VALUES
                ('event_a1', 'app_a', '2026-04', 'Alpha Login Bonus', 'Retention / Free Rewards',
                 '2026-04-10', '2026-04-20', 'Login to claim rewards.', 'fb_post', 0.91, 'gpt-5.4', 'v1', '2026-06-12T00:00:00Z', '2026-06-12T00:00:00Z'),
                ('event_a2', 'app_a', '2026-04', 'April Patch Launch', 'Release / Update Rollout',
                 NULL, NULL, 'Version rollout campaign.', 'st_app_update_event', 0.88, 'gpt-5.4', 'v1', '2026-06-12T00:00:00Z', '2026-06-12T00:00:00Z'),
                ('event_a3', 'app_a', '2026-04', 'Single Day Trial', 'Gameplay / Content Activation',
                 NULL, '2026-04-15', 'Try the event mode.', 'fb_post', 0.76, 'gpt-5.4', 'v1', '2026-06-12T00:00:00Z', '2026-06-12T00:00:00Z'),
                ('event_a4', 'app_a', '2026-05', 'May Social Push', 'Community Participation',
                 '2026-05-03', '2026-05-07', 'Share screenshots for rewards.', 'fb_post', 0.72, 'gpt-5.4', 'v1', '2026-06-12T00:00:00Z', '2026-06-12T00:00:00Z'),
                ('event_b1', 'app_b', '2026-04', 'Bravo Pass', 'Progression / Season Systems',
                 '2026-04-01', '2026-04-30', 'Monthly pass rewards.', 'st_version_event', 0.84, 'gpt-5.4', 'v1', '2026-06-12T00:00:00Z', '2026-06-12T00:00:00Z')
            """
        )
        conn.execute(
            """
            INSERT INTO unified_event_sources (
                unified_event_id, source_type, source_id, source_time, source_post_id, source_confidence
            )
            VALUES
                ('event_a1', 'fb_post', 'post_a1', '2026-04-09T08:00:00Z', 'post_a1', 0.8),
                ('event_a1', 'fb_post', 'post_a2', '2026-04-21T08:00:00Z', 'post_a2', 0.7),
                ('event_a1', 'st_app_update_event', 'st_app_a1', '2026-04-10T00:00:00Z', NULL, 1.0),
                ('event_a2', 'st_app_update_event', 'st_app_a2', '2026-04-04T00:00:00Z', NULL, 1.0),
                ('event_a3', 'fb_post', 'post_a3', '2026-04-15T09:00:00Z', 'post_a3', 0.6),
                ('event_a4', 'fb_post', 'post_a4', '2026-05-04T09:00:00Z', 'post_a4', 0.6),
                ('event_b1', 'st_version_event', 'st_ver_b1', '2026-04-01T00:00:00Z', NULL, 1.0)
            """
        )
        conn.execute(
            """
            INSERT INTO raw_fb_posts (
                source_post_id, unified_app_id, fb_page_id, channel_id, channel_name, post_type,
                post_description, duration, link, publish_time, hashtag, engagement, reaction,
                comment, share, view, source_file, ingested_at
            )
            VALUES
                ('post_a1', 'app_a', 'page_a', 'channel_a', 'Game A Page', 'photo',
                 'Alpha event launch', '', 'https://example.com/a1', '2026-04-09T08:00:00Z', '', '120', '10', '2', '1', '100', 'seed.csv', '2026-06-12T00:00:00Z'),
                ('post_a2', 'app_a', 'page_a', 'channel_a', 'Game A Page', 'photo',
                 'Alpha reminder', '', 'https://example.com/a2', '2026-04-21T08:00:00Z', '', '80', '5', '3', '0', '30', 'seed.csv', '2026-06-12T00:00:00Z'),
                ('post_a3', 'app_a', 'page_a', 'channel_a', 'Game A Page', 'video',
                 'Single day mode', '', 'https://example.com/a3', '2026-04-15T09:00:00Z', '', '1,000', '100', '20', '5', '500', 'seed.csv', '2026-06-12T00:00:00Z'),
                ('post_a4', 'app_a', 'page_a', 'channel_a', 'Game A Page', 'video',
                 'May social push', '', 'https://example.com/a4', '2026-05-04T09:00:00Z', '', '70', '8', '4', '2', '10', 'seed.csv', '2026-06-12T00:00:00Z')
            """
        )
        conn.execute(
            """
            INSERT INTO st_app_update_events (
                st_update_event_id, event_id, source_row_id, unified_app_id, event_name,
                estimated_start_date, estimated_end_date, event_description, source_refs
            )
            VALUES
                ('st_app_a1', 'alpha_login_bonus', 'raw_st_update_a1', 'app_a', 'Alpha Login Bonus',
                 '2026-04-10', '2026-04-20', 'Login to claim rewards.', '[]'),
                ('st_app_a2', 'april_patch_launch', 'raw_st_update_a2', 'app_a', 'April Patch Launch',
                 NULL, NULL, 'Version rollout campaign.', '[]')
            """
        )
        conn.execute(
            """
            INSERT INTO st_version_events (
                st_version_event_id, source_row_id, unified_app_id, event_name,
                estimated_start_date, estimated_end_date, event_description, source_refs
            )
            VALUES
                ('st_ver_b1', 'raw_st_version_b1', 'app_b', 'Bravo Pass',
                 '2026-04-01', '2026-04-30', 'Monthly pass rewards.', '[]')
            """
        )

    def test_get_events_groups_by_app_and_returns_metrics(self) -> None:
        response = self.client.get(
            "/api/events",
            params=[
                ("unified_app_id", "app_a"),
                ("unified_app_id", "app_b"),
                ("time_range_start", "2026-04-01"),
                ("time_range_end", "2026-04-30"),
            ],
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(len(payload["results"]), 2)

        app_a = payload["results"][0]
        self.assertEqual(app_a["unified_app_id"], "app_a")
        self.assertEqual(app_a["app_name"], "Game A")
        self.assertEqual([event["canonical_event_name"] for event in app_a["events"]], [
            "April Patch Launch",
            "Alpha Login Bonus",
            "Single Day Trial",
        ])

        alpha_event = next(event for event in app_a["events"] if event["unified_event_id"] == "event_a1")
        self.assertEqual(alpha_event["fb_post_count"], 2)
        self.assertEqual(alpha_event["st_app_update_event_count"], 1)
        self.assertEqual(alpha_event["st_version_event_count"], 0)
        self.assertEqual(alpha_event["total_engagement_fb"], 200)
        self.assertEqual(alpha_event["total_reaction_fb"], 15)
        self.assertEqual(alpha_event["total_comment_fb"], 5)
        self.assertEqual(alpha_event["total_share_fb"], 1)
        self.assertEqual(alpha_event["total_view_fb"], 130)
        self.assertEqual(alpha_event["social_score"], 180)

        patch_launch = next(event for event in app_a["events"] if event["unified_event_id"] == "event_a2")
        self.assertEqual(patch_launch["estimated_start_date"], None)
        self.assertEqual(patch_launch["estimated_end_date"], None)

        app_b = payload["results"][1]
        self.assertEqual(app_b["unified_app_id"], "app_b")
        self.assertEqual(app_b["app_name"], "Game B")
        self.assertEqual(len(app_b["events"]), 1)

    def test_get_events_supports_top_ranking_by_social_score(self) -> None:
        response = self.client.get(
            "/api/events",
            params=[
                ("unified_app_id", "app_a"),
                ("time_range_start", "2026-04-01"),
                ("time_range_end", "2026-04-30"),
                ("top", "2"),
            ],
        )
        self.assertEqual(response.status_code, 200)
        events = response.json()["results"][0]["events"]
        self.assertEqual([event["unified_event_id"] for event in events], ["event_a3", "event_a1"])

    def test_get_events_light_returns_compact_event_rows(self) -> None:
        response = self.client.get(
            "/api/events-light",
            params=[
                ("unified_app_id", "app_a"),
                ("time_range_start", "2026-04-01"),
                ("time_range_end", "2026-04-30"),
            ],
        )
        self.assertEqual(response.status_code, 200)
        events = response.json()["results"][0]["events"]
        self.assertEqual(
            events,
            [
                {
                    "unified_event_id": "event_a2",
                    "canonical_event_name": "April Patch Launch",
                    "event_category": "Release / Update Rollout",
                },
                {
                    "unified_event_id": "event_a1",
                    "canonical_event_name": "Alpha Login Bonus",
                    "event_category": "Retention / Free Rewards",
                },
                {
                    "unified_event_id": "event_a3",
                    "canonical_event_name": "Single Day Trial",
                    "event_category": "Gameplay / Content Activation",
                },
            ],
        )

    def test_get_games_lists_known_games(self) -> None:
        response = self.client.get("/api/games")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(
            payload["results"],
            [
                {"unified_app_id": "app_a", "app_name": "Game A"},
                {"unified_app_id": "app_b", "app_name": "Game B"},
                {"unified_app_id": "mlbb_app", "app_name": "Mobile Legends: Bang Bang"},
            ],
        )

    def test_get_games_supports_search(self) -> None:
        response = self.client.get("/api/games", params={"q": "game a"})
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(
            payload["results"],
            [
                {"unified_app_id": "app_a", "app_name": "Game A"},
            ],
        )

    def test_get_games_supports_acronym_search(self) -> None:
        response = self.client.get("/api/games", params={"q": "MLBB"})
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(
            payload["results"],
            [
                {"unified_app_id": "mlbb_app", "app_name": "Mobile Legends: Bang Bang"},
            ],
        )

    def test_get_events_uses_month_bucket_filtering(self) -> None:
        response = self.client.get(
            "/api/events",
            params=[
                ("unified_app_id", "app_a"),
                ("time_range_start", "2026-04-15"),
                ("time_range_end", "2026-04-15"),
            ],
        )
        self.assertEqual(response.status_code, 200)
        events = response.json()["results"][0]["events"]
        self.assertEqual({event["unified_event_id"] for event in events}, {"event_a2", "event_a3", "event_a1"})

    def test_get_events_uses_month_bucket_even_for_mid_month_query(self) -> None:
        response = self.client.get(
            "/api/events",
            params=[
                ("unified_app_id", "app_a"),
                ("time_range_start", "2026-05-15"),
                ("time_range_end", "2026-05-15"),
            ],
        )
        self.assertEqual(response.status_code, 200)
        events = response.json()["results"][0]["events"]
        self.assertEqual([event["unified_event_id"] for event in events], ["event_a4"])

    def test_get_event_statistics_returns_counts_and_top_events(self) -> None:
        response = self.client.get(
            "/api/event-statistics",
            params=[
                ("unified_app_id", "app_a"),
                ("time_range_start", "2026-04-01"),
                ("time_range_end", "2026-04-30"),
            ],
        )
        self.assertEqual(response.status_code, 200)
        statistics = response.json()["results"][0]["statistics"]
        self.assertEqual(statistics["event_count_total"], 3)
        self.assertEqual(statistics["event_count_st_app_update"], 2)
        self.assertEqual(statistics["event_count_st_version"], 0)
        self.assertEqual(statistics["event_count_fb"], 2)
        self.assertEqual(statistics["total_engagement_fb"], 1200)
        self.assertEqual(statistics["total_reaction_fb"], 115)
        self.assertEqual(statistics["total_comment_fb"], 25)
        self.assertEqual(statistics["total_share_fb"], 6)
        self.assertEqual(statistics["total_view_fb"], 630)
        self.assertEqual(
            [event["unified_event_id"] for event in statistics["top_socially_active_events"]],
            ["event_a3", "event_a1", "event_a2"],
        )
        self.assertEqual(statistics["top_socially_active_events"][0]["social_score"], 785)

    def test_get_event_detail_returns_full_event_payload(self) -> None:
        response = self.client.get("/api/events/event_a1")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["unified_event_id"], "event_a1")
        self.assertEqual(payload["app_name"], "Game A")
        self.assertEqual(payload["canonical_event_name"], "Alpha Login Bonus")
        self.assertEqual(payload["fb_post_count"], 2)
        self.assertEqual(payload["total_engagement_fb"], 200)
        self.assertEqual(payload["social_score"], 180)

    def test_get_event_sources_returns_fb_statistics_only(self) -> None:
        response = self.client.get("/api/events/event_a1/sources")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["unified_event_id"], "event_a1")
        self.assertEqual(payload["unified_app_id"], "app_a")
        self.assertEqual(payload["app_name"], "Game A")
        self.assertEqual(payload["canonical_event_name"], "Alpha Login Bonus")
        self.assertEqual(payload["fb_post_count"], 2)
        self.assertEqual(payload["total_reaction_fb"], 15)
        self.assertEqual(payload["total_comment_fb"], 5)
        self.assertEqual(payload["total_share_fb"], 1)
        self.assertEqual(payload["total_view_fb"], 130)
        self.assertEqual(payload["social_score"], 180)

    def test_get_event_top_posts_returns_ranked_posts(self) -> None:
        response = self.client.get("/api/events/event_a1/top-posts", params={"top": 1})
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["unified_event_id"], "event_a1")
        self.assertEqual(len(payload["posts"]), 1)
        self.assertEqual(payload["posts"][0]["source_post_id"], "post_a1")
        self.assertEqual(payload["posts"][0]["link"], "https://example.com/a1")
        self.assertEqual(payload["posts"][0]["social_score"], 131)

    def test_get_event_posts_returns_compact_post_list(self) -> None:
        response = self.client.get("/api/events/event_a1/posts")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["unified_event_id"], "event_a1")
        self.assertEqual(
            payload["posts"],
            [
                {
                    "source_post_id": "post_a1",
                    "publish_time": "2026-04-09T08:00:00Z",
                    "engagement_num": 120,
                    "reaction_num": 10,
                    "comment_num": 2,
                    "share_num": 1,
                    "view_num": 100,
                    "social_score": 131,
                },
                {
                    "source_post_id": "post_a2",
                    "publish_time": "2026-04-21T08:00:00Z",
                    "engagement_num": 80,
                    "reaction_num": 5,
                    "comment_num": 3,
                    "share_num": 0,
                    "view_num": 30,
                    "social_score": 49,
                },
            ],
        )

    def test_get_post_detail_returns_full_post_payload(self) -> None:
        response = self.client.get("/api/posts/post_a3")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["source_post_id"], "post_a3")
        self.assertEqual(payload["app_name"], "Game A")
        self.assertEqual(payload["link"], "https://example.com/a3")
        self.assertEqual(payload["engagement_num"], 1000)
        self.assertEqual(payload["reaction_num"], 100)
        self.assertEqual(payload["comment_num"], 20)
        self.assertEqual(payload["share_num"], 5)
        self.assertEqual(payload["view_num"], 500)
        self.assertEqual(payload["social_score"], 785)

    def test_get_event_detail_returns_404_for_unknown_event(self) -> None:
        response = self.client.get("/api/events/unknown_event")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["detail"], "Unified event not found.")

    def test_get_event_sources_returns_404_for_unknown_event(self) -> None:
        response = self.client.get("/api/events/unknown_event/sources")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["detail"], "Unified event not found.")

    def test_get_post_detail_returns_404_for_unknown_post(self) -> None:
        response = self.client.get("/api/posts/unknown_post")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["detail"], "Post not found.")

    def test_returns_empty_blocks_for_valid_app_without_matches(self) -> None:
        response = self.client.get(
            "/api/events",
            params=[
                ("unified_app_id", "app_b"),
                ("time_range_start", "2026-06-01"),
                ("time_range_end", "2026-06-30"),
            ],
        )
        self.assertEqual(response.status_code, 200)
        block = response.json()["results"][0]
        self.assertEqual(block["app_name"], "Game B")
        self.assertEqual(block["events"], [])

    def test_rejects_invalid_time_range(self) -> None:
        response = self.client.get(
            "/api/events",
            params=[
                ("unified_app_id", "app_a"),
                ("time_range_start", "2026-04-30"),
                ("time_range_end", "2026-04-01"),
            ],
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("time_range_start", response.json()["detail"])

    def test_rejects_missing_app_ids(self) -> None:
        response = self.client.get(
            "/api/events",
            params={"time_range_start": "2026-04-01", "time_range_end": "2026-04-30"},
        )
        self.assertEqual(response.status_code, 422)

    def test_rejects_invalid_dates(self) -> None:
        response = self.client.get(
            "/api/events",
            params=[
                ("unified_app_id", "app_a"),
                ("time_range_start", "not-a-date"),
                ("time_range_end", "2026-04-30"),
            ],
        )
        self.assertEqual(response.status_code, 422)


if __name__ == "__main__":
    unittest.main()
