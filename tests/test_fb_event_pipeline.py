from __future__ import annotations

from contextlib import contextmanager
import json
import shutil
import unittest
from pathlib import Path

from vn_event_dw.etl import init_db, open_connection
from vn_event_dw.fb_event_ai import LlmUsageRecord
from vn_event_dw.fb_event_pipeline import (
    build_fb_event_detection,
    build_fb_event_objects,
    build_fb_events,
    build_fb_events_with_llm_merge,
    build_fb_raw_events,
    build_unified_events_with_llm_merge,
    preview_fb_event_dedup,
    rerun_unified_step5,
)


class FakeFbEventClient:
    model = "chatgpt-5.4-nano"
    merge_model = "gpt-5.4-mini"
    unified_merge_model = "gpt-5.4-mini"

    def __init__(self) -> None:
        self.judge_calls = 0
        self._usage_recorder = None
        self._usage_context_stack: list[dict[str, str | None]] = []

    def set_usage_recorder(self, recorder) -> None:
        self._usage_recorder = recorder

    @contextmanager
    def usage_context(self, **kwargs):
        self._usage_context_stack.append({key: (str(value) if value is not None else None) for key, value in kwargs.items()})
        try:
            yield
        finally:
            self._usage_context_stack.pop()

    def _emit_usage(self, *, model: str, prompt_version: str) -> None:
        if self._usage_recorder is None:
            return
        context = self._usage_context_stack[-1] if self._usage_context_stack else {}
        self._usage_recorder(
            LlmUsageRecord(
                session_id=context.get("session_id"),
                run_id=context.get("run_id"),
                unified_app_id=context.get("unified_app_id"),
                month_bucket=context.get("month_bucket"),
                stage=context.get("stage") or "test",
                item_id=context.get("item_id"),
                provider="test",
                model=model,
                prompt_version=prompt_version,
                response_id="resp_test",
                input_tokens=100,
                cached_input_tokens=0,
                uncached_input_tokens=100,
                output_tokens=20,
                total_tokens=120,
                input_cost_usd=0.01,
                cached_input_cost_usd=0.0,
                output_cost_usd=0.02,
                total_cost_usd=0.03,
                created_at="2026-06-01T00:00:00+00:00",
            )
        )

    def detect_post_event(self, post_payload: dict[str, object]) -> dict[str, object]:
        self._emit_usage(model=self.model, prompt_version="fb_post_event_detection_v3")
        post_id = str(post_payload["post_id"])
        post_text = str(post_payload["post_text"])
        if post_id == "post_003":
            return {
                "post_id": post_id,
                "contains_event": False,
                "confidence": 0.2,
                "reason": "Generic engagement post.",
                "event_signals": [],
            }
        if "UPDATED" in post_text:
            signals = ["updated_campaign_copy"]
        else:
            signals = ["minigame_campaign"]
        return {
            "post_id": post_id,
            "contains_event": True,
            "confidence": 0.91,
            "reason": "Concrete campaign mechanics are present.",
            "event_signals": signals,
        }

    def extract_event_objects(self, post_payload: dict[str, object]) -> dict[str, object]:
        self._emit_usage(model=self.model, prompt_version="fb_post_event_extraction_v3")
        post_id = str(post_payload["post_id"])
        post_text = str(post_payload["post_text"])
        if post_id == "post_001" and "UPDATED" in post_text:
            description = "Updated Summer Cup minigame mechanics."
            evidence = "UPDATED Summer Cup minigame"
        elif post_id == "post_001":
            description = "Join the Summer Cup minigame by commenting and sharing."
            evidence = "Summer Cup minigame"
        else:
            description = "Reminder to join the Summer Cup minigame."
            evidence = "Reminder for Summer Cup"
        return {
            "post_id": post_id,
            "events": [
                {
                    "event_name": "Summer Cup Minigame",
                    "estimated_start_date": "2026-06-01",
                    "estimated_end_date": "2026-06-07",
                    "event_description": description,
                    "evidence_text": evidence,
                    "confidence": 0.95,
                }
            ],
        }

    def judge_event_pair(self, pair_payload: dict[str, object]) -> dict[str, object]:
        self._emit_usage(model=self.model, prompt_version="fb_post_event_dedup_v1")
        self.judge_calls += 1
        left = pair_payload["event_a"]  # type: ignore[index]
        right = pair_payload["event_b"]  # type: ignore[index]
        same_event = left["event_name"] == right["event_name"]  # type: ignore[index]
        return {
            "same_event": same_event,
            "confidence": 0.93 if same_event else 0.12,
            "reason": "Matching named campaign across reminder posts." if same_event else "Different event.",
        }

    def merge_event_objects(self, merge_payload: dict[str, object]) -> dict[str, object]:
        self._emit_usage(model=self.merge_model, prompt_version="fb_post_event_merge_v1")
        raw_objects = merge_payload["raw_event_objects"]  # type: ignore[index]
        merged: dict[str, dict[str, object]] = {}
        for item in raw_objects:  # type: ignore[assignment]
            event_name = str(item["event_name"])  # type: ignore[index]
            bucket = "Summer Cup Minigame" if "Summer Cup" in event_name else event_name
            current = merged.setdefault(
                bucket,
                {
                    "canonical_event_name": bucket,
                    "estimated_start_date": item["estimated_start_date"],  # type: ignore[index]
                    "estimated_end_date": item["estimated_end_date"],  # type: ignore[index]
                    "canonical_event_description": str(item["event_description"]),  # type: ignore[index]
                    "source_event_object_ids": [],
                    "dedup_confidence": 0.9,
                },
            )
            current["source_event_object_ids"].append(str(item["event_object_id"]))  # type: ignore[index]
        return {"events": list(merged.values())}

    def merge_unified_event_sources(self, merge_payload: dict[str, object]) -> dict[str, object]:
        self._emit_usage(model=self.unified_merge_model, prompt_version="unified_cross_source_event_merge_v5")
        source_events = merge_payload["source_events"]  # type: ignore[index]
        st_update_ids: list[str] = []
        fb_ids: list[str] = []
        version_ids: list[str] = []
        for item in source_events:  # type: ignore[assignment]
            source_type = str(item["source_type"])  # type: ignore[index]
            source_id = str(item["source_id"])  # type: ignore[index]
            if source_type == "st_app_update_event":
                st_update_ids.append(source_id)
            elif source_type == "fb_post":
                fb_ids.append(source_id)
            elif source_type == "st_version_event":
                version_ids.append(source_id)

        events: list[dict[str, object]] = []
        if st_update_ids or fb_ids:
            events.append(
                {
                    "canonical_event_name": "Summer Cup Minigame",
                    "event_category": "Community Participation",
                    "estimated_start_date": "2026-06-01",
                    "estimated_end_date": "2026-06-07",
                    "canonical_event_description": "Summer Cup campaign merged from ST and Facebook evidence.",
                    "anchor_source_type": "st_app_update_event",
                    "source_ids": st_update_ids + fb_ids,
                    "merge_confidence": 0.94,
                }
            )
        if version_ids:
            events.append(
                {
                    "canonical_event_name": "android Version Update 1.2.3",
                    "event_category": "Other",
                    "estimated_start_date": "2026-06-05",
                    "estimated_end_date": "2026-06-05",
                    "canonical_event_description": "Standalone version update.",
                    "anchor_source_type": "st_version_event",
                    "source_ids": version_ids,
                    "merge_confidence": 0.91,
                }
            )
        return {"events": events, "discarded_source_ids": []}

    def consolidate_unified_candidates(self, merge_payload: dict[str, object]) -> dict[str, object]:
        self._emit_usage(model=self.unified_merge_model, prompt_version="unified_cross_source_event_consolidation_v2")
        candidate_events = merge_payload["candidate_events"]  # type: ignore[index]
        events: list[dict[str, object]] = []
        for item in candidate_events:  # type: ignore[assignment]
            events.append(
                {
                    "canonical_event_name": str(item["event_name"]),  # type: ignore[index]
                    "event_category": str(item["event_category"]),  # type: ignore[index]
                    "estimated_start_date": item["estimated_start_date"],  # type: ignore[index]
                    "estimated_end_date": item["estimated_end_date"],  # type: ignore[index]
                    "canonical_event_description": str(item["event_description"]),  # type: ignore[index]
                    "anchor_source_type": str(item["source_type"]),  # type: ignore[index]
                    "source_ids": [str(item["source_id"])],  # type: ignore[index]
                    "merge_confidence": float(item["source_confidence"]),  # type: ignore[index]
                }
            )
        return {"events": events}

    def harvest_remaining_fb_post_events(self, post_payload: dict[str, object]) -> dict[str, object]:
        self._emit_usage(model=self.unified_merge_model, prompt_version="fb_remaining_event_harvest_v2")
        return {"post_id": str(post_payload["post_id"]), "events": []}


class FbEventPipelineTests(unittest.TestCase):
    def test_fb_event_pipeline_end_to_end_and_incremental_rerun(self) -> None:
        temp_root = Path.cwd() / "_tmp_fb_event_pipeline_test"
        db_path = temp_root / "warehouse.db"
        shutil.rmtree(temp_root, ignore_errors=True)
        temp_root.mkdir(parents=True, exist_ok=True)

        try:
            conn = open_connection(db_path)
            try:
                init_db(conn)
                conn.execute(
                    """
                    INSERT INTO config_app_mapping (
                        unified_app_id, fb_page_id, app_name, is_active
                    ) VALUES (?, ?, ?, ?)
                    """,
                    ("57955d280211a6718a000002", "1722990624697290", "Mobile Legends: Bang Bang", 1),
                )
                conn.executemany(
                    """
                    INSERT INTO raw_fb_posts (
                        source_post_id, fb_page_id, channel_id, channel_name, post_type,
                        post_description, duration, link, publish_time, hashtag,
                        engagement, reaction, comment, share, view, source_file, ingested_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    [
                        (
                            "post_001",
                            "1722990624697290",
                            "1722990624697290",
                            "Mobile Legends Game VN",
                            "photo",
                            "Summer Cup minigame is live. Comment and share to win diamonds.",
                            "",
                            "https://example.com/post-1",
                            "2026-06-01T08:00:00+07:00",
                            "#summercup",
                            "100",
                            "50",
                            "10",
                            "5",
                            "35",
                            "fb_posts/game1.csv",
                            "2026-06-01T09:00:00+07:00",
                        ),
                        (
                            "post_002",
                            "1722990624697290",
                            "1722990624697290",
                            "Mobile Legends Game VN",
                            "photo",
                            "Reminder for Summer Cup minigame. Ends this week.",
                            "",
                            "https://example.com/post-2",
                            "2026-06-03T08:00:00+07:00",
                            "#summercup",
                            "",
                            "20",
                            "4",
                            "2",
                            "14",
                            "fb_posts/game1.csv",
                            "2026-06-03T09:00:00+07:00",
                        ),
                        (
                            "post_003",
                            "1722990624697290",
                            "1722990624697290",
                            "Mobile Legends Game VN",
                            "photo",
                            "How is your week going, legends?",
                            "",
                            "https://example.com/post-3",
                            "2026-06-04T08:00:00+07:00",
                            "",
                            "12",
                            "6",
                            "2",
                            "1",
                            "3",
                            "fb_posts/game1.csv",
                            "2026-06-04T09:00:00+07:00",
                        ),
                    ],
                )
                conn.commit()

                client = FakeFbEventClient()

                detection_stats = build_fb_event_detection(conn, client=client)
                self.assertEqual(detection_stats.processed_posts, 3)
                self.assertEqual(detection_stats.detected_posts, 2)

                object_stats = build_fb_event_objects(conn, client=client)
                self.assertEqual(object_stats.processed_posts, 2)
                self.assertEqual(object_stats.extracted_objects, 2)

                event_stats = build_fb_events(conn, client=client)
                self.assertEqual(event_stats.candidate_pairs, 1)
                self.assertEqual(event_stats.judged_pairs, 0)
                self.assertEqual(event_stats.fb_events, 1)
                self.assertEqual(client.judge_calls, 0)

                detection_rows = conn.execute("SELECT COUNT(*) FROM post_event_detection").fetchone()[0]
                object_rows = conn.execute("SELECT COUNT(*) FROM post_event_objects").fetchone()[0]
                match_rows = conn.execute("SELECT COUNT(*) FROM fb_event_match_decisions").fetchone()[0]
                fb_events_rows = conn.execute("SELECT COUNT(*) FROM fb_events").fetchone()[0]
                self.assertEqual(detection_rows, 3)
                self.assertEqual(object_rows, 2)
                self.assertEqual(match_rows, 1)
                self.assertEqual(fb_events_rows, 1)

                fb_event = conn.execute("SELECT * FROM fb_events").fetchone()
                self.assertEqual(fb_event["canonical_event_name"], "Summer Cup Minigame")
                self.assertEqual(fb_event["estimated_start_date"], "2026-06-01")
                self.assertEqual(fb_event["estimated_end_date"], "2026-06-07")
                self.assertEqual(fb_event["game_name"], "Mobile Legends: Bang Bang")
                self.assertEqual(fb_event["page_name"], "Mobile Legends Game VN")
                self.assertEqual(fb_event["num_source_posts"], 2)
                self.assertEqual(fb_event["total_engagement"], 140)
                self.assertEqual(json.loads(fb_event["source_post_ids"]), ["post_001", "post_002"])
                decision_row = conn.execute("SELECT decision_source FROM fb_event_match_decisions").fetchone()
                self.assertEqual(decision_row["decision_source"], "rule_merge")

                detection_stats_second = build_fb_event_detection(conn, client=client)
                object_stats_second = build_fb_event_objects(conn, client=client)
                event_stats_second = build_fb_events(conn, client=client)
                self.assertEqual(detection_stats_second.processed_posts, 0)
                self.assertEqual(object_stats_second.processed_posts, 0)
                self.assertEqual(event_stats_second.candidate_pairs, 1)
                self.assertEqual(event_stats_second.judged_pairs, 0)
                self.assertEqual(event_stats_second.fb_events, 1)
                self.assertEqual(client.judge_calls, 0)
            finally:
                conn.close()
        finally:
            shutil.rmtree(temp_root, ignore_errors=True)

    def test_changed_post_text_forces_redetection_and_reextraction(self) -> None:
        temp_root = Path.cwd() / "_tmp_fb_event_pipeline_update_test"
        db_path = temp_root / "warehouse.db"
        shutil.rmtree(temp_root, ignore_errors=True)
        temp_root.mkdir(parents=True, exist_ok=True)

        try:
            conn = open_connection(db_path)
            try:
                init_db(conn)
                conn.execute(
                    """
                    INSERT INTO config_app_mapping (
                        unified_app_id, fb_page_id, app_name, is_active
                    ) VALUES (?, ?, ?, ?)
                    """,
                    ("57955d280211a6718a000002", "1722990624697290", "Mobile Legends: Bang Bang", 1),
                )
                conn.execute(
                    """
                    INSERT INTO raw_fb_posts (
                        source_post_id, fb_page_id, channel_id, channel_name, post_type,
                        post_description, duration, link, publish_time, hashtag,
                        engagement, reaction, comment, share, view, source_file, ingested_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        "post_001",
                        "1722990624697290",
                        "1722990624697290",
                        "Mobile Legends Game VN",
                        "photo",
                        "Summer Cup minigame is live.",
                        "",
                        "https://example.com/post-1",
                        "2026-06-01T08:00:00+07:00",
                        "",
                        "100",
                        "50",
                        "10",
                        "5",
                        "35",
                        "fb_posts/game1.csv",
                        "2026-06-01T09:00:00+07:00",
                    ),
                )
                conn.commit()

                client = FakeFbEventClient()
                build_fb_event_detection(conn, client=client)
                build_fb_event_objects(conn, client=client)
                original_object_id = conn.execute("SELECT event_object_id FROM post_event_objects").fetchone()[0]

                conn.execute(
                    "UPDATE raw_fb_posts SET post_description = ? WHERE source_post_id = ?",
                    ("UPDATED Summer Cup minigame is live.", "post_001"),
                )
                conn.commit()

                detection_stats = build_fb_event_detection(conn, client=client)
                object_stats = build_fb_event_objects(conn, client=client)
                self.assertEqual(detection_stats.processed_posts, 1)
                self.assertEqual(object_stats.processed_posts, 1)

                new_object_id = conn.execute("SELECT event_object_id FROM post_event_objects").fetchone()[0]
                self.assertNotEqual(original_object_id, new_object_id)
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM post_event_objects").fetchone()[0], 1)
            finally:
                conn.close()
        finally:
            shutil.rmtree(temp_root, ignore_errors=True)

    def test_ambiguous_pairs_still_use_llm_and_cache(self) -> None:
        temp_root = Path.cwd() / "_tmp_fb_event_pipeline_ambiguous_test"
        db_path = temp_root / "warehouse.db"
        shutil.rmtree(temp_root, ignore_errors=True)
        temp_root.mkdir(parents=True, exist_ok=True)

        class AmbiguousJudgeClient(FakeFbEventClient):
            def judge_event_pair(self, pair_payload: dict[str, object]) -> dict[str, object]:
                self.judge_calls += 1
                return {
                    "same_event": True,
                    "confidence": 0.88,
                    "reason": "Reminder and announcement refer to the same campaign.",
                }

        try:
            conn = open_connection(db_path)
            try:
                init_db(conn)
                conn.execute(
                    """
                    INSERT INTO config_app_mapping (
                        unified_app_id, fb_page_id, app_name, is_active
                    ) VALUES (?, ?, ?, ?)
                    """,
                    ("57955d280211a6718a000002", "1722990624697290", "Mobile Legends: Bang Bang", 1),
                )
                conn.execute(
                    """
                    INSERT INTO raw_fb_posts (
                        source_post_id, fb_page_id, channel_id, channel_name, post_type,
                        post_description, duration, link, publish_time, hashtag,
                        engagement, reaction, comment, share, view, source_file, ingested_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        "post_010",
                        "1722990624697290",
                        "1722990624697290",
                        "Mobile Legends Game VN",
                        "photo",
                        "placeholder",
                        "",
                        "https://example.com/post-10",
                        "2026-06-10T08:00:00+07:00",
                        "",
                        "12",
                        "4",
                        "2",
                        "1",
                        "5",
                        "fb_posts/game1.csv",
                        "2026-06-10T09:00:00+07:00",
                    ),
                )
                conn.execute(
                    """
                    INSERT INTO raw_fb_posts (
                        source_post_id, fb_page_id, channel_id, channel_name, post_type,
                        post_description, duration, link, publish_time, hashtag,
                        engagement, reaction, comment, share, view, source_file, ingested_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        "post_011",
                        "1722990624697290",
                        "1722990624697290",
                        "Mobile Legends Game VN",
                        "photo",
                        "placeholder",
                        "",
                        "https://example.com/post-11",
                        "2026-06-13T08:00:00+07:00",
                        "",
                        "10",
                        "3",
                        "2",
                        "1",
                        "4",
                        "fb_posts/game1.csv",
                        "2026-06-13T09:00:00+07:00",
                    ),
                )
                conn.execute(
                    """
                    INSERT INTO post_event_objects (
                        event_object_id, post_id, fb_page_id, page_name, game_name, post_time,
                        event_name, estimated_start_date, estimated_end_date, event_description,
                        evidence_text, extraction_confidence, llm_model, prompt_version, processed_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        "obj_010",
                        "post_010",
                        "1722990624697290",
                        "Mobile Legends Game VN",
                        "Mobile Legends: Bang Bang",
                        "2026-06-10T08:00:00+07:00",
                        "Summer Cup Finals",
                        "2026-06-15",
                        "2026-06-20",
                        "Join the finals event by watching the stream and claiming rewards.",
                        "Summer Cup Finals",
                        0.92,
                        "chatgpt-5.4-nano",
                        "test",
                        "2026-06-10T09:00:00+07:00",
                    ),
                )
                conn.execute(
                    """
                    INSERT INTO post_event_objects (
                        event_object_id, post_id, fb_page_id, page_name, game_name, post_time,
                        event_name, estimated_start_date, estimated_end_date, event_description,
                        evidence_text, extraction_confidence, llm_model, prompt_version, processed_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        "obj_011",
                        "post_011",
                        "1722990624697290",
                        "Mobile Legends Game VN",
                        "Mobile Legends: Bang Bang",
                        "2026-06-13T08:00:00+07:00",
                        "Summer Cup Finals Reminder",
                        "2026-06-15",
                        "2026-06-20",
                        "Reminder to watch the finals stream and claim event rewards.",
                        "Summer Cup Finals Reminder",
                        0.90,
                        "chatgpt-5.4-nano",
                        "test",
                        "2026-06-13T09:00:00+07:00",
                    ),
                )
                conn.commit()

                client = AmbiguousJudgeClient()
                event_stats = build_fb_events(conn, client=client)
                self.assertEqual(event_stats.candidate_pairs, 1)
                self.assertEqual(event_stats.judged_pairs, 1)
                self.assertEqual(client.judge_calls, 1)
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM fb_events").fetchone()[0], 1)

                decision_row = conn.execute("SELECT decision_source FROM fb_event_match_decisions").fetchone()
                self.assertEqual(decision_row["decision_source"], "llm_judge")

                event_stats_second = build_fb_events(conn, client=client)
                self.assertEqual(event_stats_second.candidate_pairs, 1)
                self.assertEqual(event_stats_second.judged_pairs, 0)
                self.assertEqual(client.judge_calls, 1)
            finally:
                conn.close()
        finally:
            shutil.rmtree(temp_root, ignore_errors=True)

    def test_preview_and_scope_controls(self) -> None:
        temp_root = Path.cwd() / "_tmp_fb_event_pipeline_scope_test"
        db_path = temp_root / "warehouse.db"
        shutil.rmtree(temp_root, ignore_errors=True)
        temp_root.mkdir(parents=True, exist_ok=True)

        try:
            conn = open_connection(db_path)
            try:
                init_db(conn)
                conn.execute(
                    """
                    INSERT INTO config_app_mapping (
                        unified_app_id, fb_page_id, app_name, is_active
                    ) VALUES (?, ?, ?, ?)
                    """,
                    ("57955d280211a6718a000002", "1722990624697290", "Mobile Legends: Bang Bang", 1),
                )
                conn.executemany(
                    """
                    INSERT INTO raw_fb_posts (
                        source_post_id, fb_page_id, channel_id, channel_name, post_type,
                        post_description, duration, link, publish_time, hashtag,
                        engagement, reaction, comment, share, view, source_file, ingested_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    [
                        (
                            "post_scope_1",
                            "1722990624697290",
                            "1722990624697290",
                            "Mobile Legends Game VN",
                            "photo",
                            "Summer Cup minigame is live.",
                            "",
                            "https://example.com/scope-1",
                            "2026-06-01T08:00:00+07:00",
                            "",
                            "20",
                            "10",
                            "2",
                            "1",
                            "7",
                            "fb_posts/game1.csv",
                            "2026-06-01T09:00:00+07:00",
                        ),
                        (
                            "post_scope_2",
                            "9999999999999999",
                            "9999999999999999",
                            "Other Page",
                            "photo",
                            "Other promotion copy.",
                            "",
                            "https://example.com/scope-2",
                            "2026-06-01T08:00:00+07:00",
                            "",
                            "20",
                            "10",
                            "2",
                            "1",
                            "7",
                            "fb_posts/game2.csv",
                            "2026-06-01T09:00:00+07:00",
                        ),
                    ],
                )
                conn.commit()

                client = FakeFbEventClient()
                detection_stats = build_fb_event_detection(
                    conn,
                    client=client,
                    fb_page_id="1722990624697290",
                    limit=1,
                )
                self.assertEqual(detection_stats.processed_posts, 1)
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM post_event_detection").fetchone()[0], 1)

                conn.executemany(
                    """
                    INSERT INTO post_event_objects (
                        event_object_id, post_id, fb_page_id, page_name, game_name, post_time,
                        event_name, estimated_start_date, estimated_end_date, event_description,
                        evidence_text, extraction_confidence, llm_model, prompt_version, processed_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    [
                        (
                            "obj_scope_1",
                            "post_scope_1",
                            "1722990624697290",
                            "Mobile Legends Game VN",
                            "Mobile Legends: Bang Bang",
                            "2026-06-01T08:00:00+07:00",
                            "Summer Cup",
                            "2026-06-05",
                            "2026-06-07",
                            "Join Summer Cup by commenting and sharing.",
                            "Summer Cup",
                            0.90,
                            "chatgpt-5.4-nano",
                            "test",
                            "2026-06-01T09:00:00+07:00",
                        ),
                        (
                            "obj_scope_2",
                            "post_scope_2",
                            "9999999999999999",
                            "Other Page",
                            "Other Game",
                            "2026-06-01T08:00:00+07:00",
                            "Other Event",
                            "2026-06-10",
                            "2026-06-12",
                            "Different event description entirely.",
                            "Other Event",
                            0.85,
                            "chatgpt-5.4-nano",
                            "test",
                            "2026-06-01T09:00:00+07:00",
                        ),
                    ],
                )
                conn.commit()

                preview_stats = preview_fb_event_dedup(conn, game_name="Mobile Legends: Bang Bang")
                self.assertEqual(preview_stats.candidate_pairs, 0)
                self.assertEqual(preview_stats.rule_merge_pairs, 0)
                self.assertEqual(preview_stats.rule_reject_pairs, 0)
                self.assertEqual(preview_stats.llm_judge_pairs, 0)
            finally:
                conn.close()
        finally:
            shutil.rmtree(temp_root, ignore_errors=True)

    def test_same_page_different_games_do_not_form_candidate_pairs(self) -> None:
        temp_root = Path.cwd() / "_tmp_fb_event_pipeline_game_blocking_test"
        db_path = temp_root / "warehouse.db"
        shutil.rmtree(temp_root, ignore_errors=True)
        temp_root.mkdir(parents=True, exist_ok=True)

        try:
            conn = open_connection(db_path)
            try:
                init_db(conn)
                conn.executemany(
                    """
                    INSERT INTO post_event_objects (
                        event_object_id, post_id, fb_page_id, page_name, game_name, post_time,
                        event_name, estimated_start_date, estimated_end_date, event_description,
                        evidence_text, extraction_confidence, llm_model, prompt_version, processed_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    [
                        (
                            "obj_game_block_1",
                            "post_game_block_1",
                            "same_page_id",
                            "Publisher Main Page",
                            "Game A",
                            "2026-06-01T08:00:00+07:00",
                            "Summer Cup",
                            "2026-06-05",
                            "2026-06-07",
                            "Game A summer cup event.",
                            "Summer Cup",
                            0.91,
                            "gpt-5.4-nano",
                            "test",
                            "2026-06-01T09:00:00+07:00",
                        ),
                        (
                            "obj_game_block_2",
                            "post_game_block_2",
                            "same_page_id",
                            "Publisher Main Page",
                            "Game B",
                            "2026-06-02T08:00:00+07:00",
                            "Summer Cup",
                            "2026-06-05",
                            "2026-06-07",
                            "Game B summer cup event.",
                            "Summer Cup",
                            0.91,
                            "gpt-5.4-nano",
                            "test",
                            "2026-06-02T09:00:00+07:00",
                        ),
                    ],
                )
                conn.commit()

                preview_stats = preview_fb_event_dedup(conn)
                self.assertEqual(preview_stats.candidate_pairs, 0)
                self.assertEqual(preview_stats.rule_merge_pairs, 0)
                self.assertEqual(preview_stats.rule_reject_pairs, 0)
                self.assertEqual(preview_stats.llm_judge_pairs, 0)
            finally:
                conn.close()
        finally:
            shutil.rmtree(temp_root, ignore_errors=True)

    def test_generic_facebook_event_link_objects_are_filtered(self) -> None:
        temp_root = Path.cwd() / "_tmp_fb_event_pipeline_filter_test"
        db_path = temp_root / "warehouse.db"
        shutil.rmtree(temp_root, ignore_errors=True)
        temp_root.mkdir(parents=True, exist_ok=True)

        class GenericLinkClient(FakeFbEventClient):
            def detect_post_event(self, post_payload: dict[str, object]) -> dict[str, object]:
                return {
                    "post_id": str(post_payload["post_id"]),
                    "contains_event": True,
                    "confidence": 0.95,
                    "reason": "Looks like an event link.",
                    "event_signals": ["facebook_event_link"],
                }

            def extract_event_objects(self, post_payload: dict[str, object]) -> dict[str, object]:
                return {
                    "post_id": str(post_payload["post_id"]),
                    "events": [
                        {
                            "event_name": "Facebook Event (link)",
                            "estimated_start_date": None,
                            "estimated_end_date": None,
                            "event_description": "Bài đăng chỉ chứa liên kết tới một Facebook Event, không có nội dung mô tả cụ thể.",
                            "evidence_text": "Facebook Event",
                            "confidence": 0.90,
                        }
                    ],
                }

        try:
            conn = open_connection(db_path)
            try:
                init_db(conn)
                conn.execute(
                    """
                    INSERT INTO config_app_mapping (
                        unified_app_id, fb_page_id, app_name, is_active
                    ) VALUES (?, ?, ?, ?)
                    """,
                    ("57955d280211a6718a000002", "1722990624697290", "Mobile Legends: Bang Bang", 1),
                )
                conn.execute(
                    """
                    INSERT INTO raw_fb_posts (
                        source_post_id, fb_page_id, channel_id, channel_name, post_type,
                        post_description, duration, link, publish_time, hashtag,
                        engagement, reaction, comment, share, view, source_file, ingested_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        "post_link_only",
                        "1722990624697290",
                        "1722990624697290",
                        "Mobile Legends Game VN",
                        "photo",
                        "",
                        "",
                        "https://www.facebook.com/events/1234567890",
                        "2026-06-01T08:00:00+07:00",
                        "",
                        "0",
                        "0",
                        "0",
                        "0",
                        "0",
                        "fb_posts/game1.csv",
                        "2026-06-01T09:00:00+07:00",
                    ),
                )
                conn.commit()

                client = GenericLinkClient()
                detection_stats = build_fb_event_detection(conn, client=client)
                object_stats = build_fb_event_objects(conn, client=client)

                self.assertEqual(detection_stats.processed_posts, 1)
                self.assertEqual(object_stats.processed_posts, 1)
                self.assertEqual(object_stats.extracted_objects, 0)
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM post_event_objects").fetchone()[0], 0)
            finally:
                conn.close()
        finally:
            shutil.rmtree(temp_root, ignore_errors=True)

    def test_build_fb_raw_events_runs_detection_and_extraction(self) -> None:
        temp_root = Path.cwd() / "_tmp_fb_raw_events_test"
        db_path = temp_root / "warehouse.db"
        shutil.rmtree(temp_root, ignore_errors=True)
        temp_root.mkdir(parents=True, exist_ok=True)

        try:
            conn = open_connection(db_path)
            try:
                init_db(conn)
                conn.execute(
                    """
                    INSERT INTO config_app_mapping (
                        unified_app_id, fb_page_id, app_name, is_active
                    ) VALUES (?, ?, ?, ?)
                    """,
                    ("57955d280211a6718a000002", "1722990624697290", "Mobile Legends: Bang Bang", 1),
                )
                conn.execute(
                    """
                    INSERT INTO raw_fb_posts (
                        source_post_id, fb_page_id, channel_id, channel_name, post_type,
                        post_description, duration, link, publish_time, hashtag,
                        engagement, reaction, comment, share, view, source_file, ingested_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        "post_raw_001",
                        "1722990624697290",
                        "1722990624697290",
                        "Mobile Legends Game VN",
                        "photo",
                        "Summer Cup minigame is live. Comment and share to win diamonds.",
                        "",
                        "https://example.com/post-raw-1",
                        "2026-06-01T08:00:00+07:00",
                        "#summercup",
                        "100",
                        "50",
                        "10",
                        "5",
                        "35",
                        "fb_posts/game1.csv",
                        "2026-06-01T09:00:00+07:00",
                    ),
                )
                conn.commit()

                client = FakeFbEventClient()
                stats = build_fb_raw_events(conn, client=client)
                self.assertEqual(stats.detection_processed_posts, 1)
                self.assertEqual(stats.detected_posts, 1)
                self.assertEqual(stats.extraction_processed_posts, 1)
                self.assertEqual(stats.extracted_objects, 1)
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM post_event_detection").fetchone()[0], 1)
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM post_event_objects").fetchone()[0], 1)
            finally:
                conn.close()
        finally:
            shutil.rmtree(temp_root, ignore_errors=True)

    def test_build_fb_events_with_llm_merge(self) -> None:
        temp_root = Path.cwd() / "_tmp_fb_llm_merge_test"
        db_path = temp_root / "warehouse.db"
        shutil.rmtree(temp_root, ignore_errors=True)
        temp_root.mkdir(parents=True, exist_ok=True)

        try:
            conn = open_connection(db_path)
            try:
                init_db(conn)
                conn.execute(
                    """
                    INSERT INTO raw_fb_posts (
                        source_post_id, fb_page_id, channel_id, channel_name, post_type,
                        post_description, duration, link, publish_time, hashtag,
                        engagement, reaction, comment, share, view, source_file, ingested_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        "post_merge_1",
                        "1722990624697290",
                        "1722990624697290",
                        "Mobile Legends Game VN",
                        "photo",
                        "Summer Cup minigame is live.",
                        "",
                        "https://example.com/post-merge-1",
                        "2026-06-01T08:00:00+07:00",
                        "",
                        "100",
                        "50",
                        "10",
                        "5",
                        "35",
                        "fb_posts/game1.csv",
                        "2026-06-01T09:00:00+07:00",
                    ),
                )
                conn.execute(
                    """
                    INSERT INTO raw_fb_posts (
                        source_post_id, fb_page_id, channel_id, channel_name, post_type,
                        post_description, duration, link, publish_time, hashtag,
                        engagement, reaction, comment, share, view, source_file, ingested_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        "post_merge_2",
                        "1722990624697290",
                        "1722990624697290",
                        "Mobile Legends Game VN",
                        "photo",
                        "Reminder for Summer Cup minigame.",
                        "",
                        "https://example.com/post-merge-2",
                        "2026-06-02T08:00:00+07:00",
                        "",
                        "50",
                        "20",
                        "5",
                        "3",
                        "22",
                        "fb_posts/game1.csv",
                        "2026-06-02T09:00:00+07:00",
                    ),
                )
                conn.executemany(
                    """
                    INSERT INTO post_event_objects (
                        event_object_id, post_id, fb_page_id, page_name, game_name, post_time,
                        event_name, estimated_start_date, estimated_end_date, event_description,
                        evidence_text, extraction_confidence, llm_model, prompt_version, processed_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    [
                        (
                            "obj_merge_1",
                            "post_merge_1",
                            "1722990624697290",
                            "Mobile Legends Game VN",
                            "Mobile Legends: Bang Bang",
                            "2026-06-01T08:00:00+07:00",
                            "Summer Cup Minigame",
                            "2026-06-01",
                            "2026-06-07",
                            "Join the Summer Cup minigame by commenting and sharing.",
                            "Summer Cup minigame",
                            0.95,
                            "gpt-5.4-nano",
                            "test",
                            "2026-06-01T09:00:00+07:00",
                        ),
                        (
                            "obj_merge_2",
                            "post_merge_2",
                            "1722990624697290",
                            "Mobile Legends Game VN",
                            "Mobile Legends: Bang Bang",
                            "2026-06-02T08:00:00+07:00",
                            "Summer Cup Reminder",
                            "2026-06-01",
                            "2026-06-07",
                            "Reminder to join the Summer Cup minigame.",
                            "Summer Cup reminder",
                            0.90,
                            "gpt-5.4-nano",
                            "test",
                            "2026-06-02T09:00:00+07:00",
                        ),
                    ],
                )
                conn.commit()

                client = FakeFbEventClient()
                stats = build_fb_events_with_llm_merge(conn, client=client)
                self.assertEqual(stats.merge_groups, 1)
                self.assertEqual(stats.source_objects, 2)
                self.assertEqual(stats.merged_events, 1)

                row = conn.execute("SELECT * FROM fb_events").fetchone()
                self.assertEqual(row["canonical_event_name"], "Summer Cup Minigame")
                self.assertEqual(json.loads(row["source_event_object_ids"]), ["obj_merge_1", "obj_merge_2"])
                self.assertEqual(row["num_source_posts"], 2)
                self.assertEqual(row["total_engagement"], 150)
            finally:
                conn.close()
        finally:
            shutil.rmtree(temp_root, ignore_errors=True)

    def test_build_unified_events_with_llm_merge_prefers_st_anchor(self) -> None:
        temp_root = Path.cwd() / "_tmp_unified_events_llm_test"
        db_path = temp_root / "warehouse.db"
        shutil.rmtree(temp_root, ignore_errors=True)
        temp_root.mkdir(parents=True, exist_ok=True)

        try:
            conn = open_connection(db_path)
            try:
                init_db(conn)
                conn.execute(
                    """
                    INSERT INTO config_app_mapping (
                        unified_app_id, fb_page_id, app_name, is_active
                    ) VALUES (?, ?, ?, ?)
                    """,
                    ("57955d280211a6718a000002", "1722990624697290", "Mobile Legends: Bang Bang", 1),
                )
                conn.execute(
                    """
                    INSERT INTO raw_fb_posts (
                        source_post_id, fb_page_id, channel_id, channel_name, post_type,
                        post_description, duration, link, publish_time, hashtag,
                        engagement, reaction, comment, share, view, source_file, ingested_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        "post_001",
                        "1722990624697290",
                        "1722990624697290",
                        "Mobile Legends Game VN",
                        "photo",
                        "Summer Cup Reminder. Join the Summer Cup event before it ends this week.",
                        "",
                        "https://example.com/post-001",
                        "2026-06-02T08:00:00+07:00",
                        "#summercup",
                        "100",
                        "50",
                        "10",
                        "5",
                        "35",
                        "fb_posts/mlbb.csv",
                        "2026-06-02T09:00:00+07:00",
                    ),
                )
                conn.execute(
                    """
                    INSERT INTO raw_st_app_update (
                        source_update_id, unified_app_id, update_time, update_type, source_file, ingested_at, events_json, events_raw, raw_payload, update_payload
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        "stupd_src_001",
                        "57955d280211a6718a000002",
                        "2026-06-01T08:00:00Z",
                        "metadata",
                        "st_update.json",
                        "2026-06-01T08:05:00Z",
                        "[]",
                        "{\"after\": [], \"before\": []}",
                        "{}",
                        "{}",
                    ),
                )
                conn.execute(
                    """
                    INSERT INTO st_app_update_events (
                        st_update_event_id, event_id, source_row_id, unified_app_id, event_name,
                        estimated_start_date, estimated_end_date, event_description, source_refs
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        "stupd_001",
                        "6773258941",
                        "stupd_src_001",
                        "57955d280211a6718a000002",
                        "Summer Cup Minigame",
                        "2026-06-01",
                        "2026-06-07",
                        "Official Summer Cup campaign from Sensor Tower.",
                        "[]",
                    ),
                )
                conn.execute(
                    """
                    INSERT INTO raw_st_version (
                        source_version_id, unified_app_id, os, app_id, country, version_time, version_name, after_version, version_summary, raw_payload, version_payload, source_file, ingested_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        "stver_src_001",
                        "57955d280211a6718a000002",
                        "android",
                        "com.example.game",
                        "VN",
                        "2026-06-05T08:00:00Z",
                        "1.2.3",
                        "1.2.3",
                        "Patch update",
                        "{}",
                        "{}",
                        "st_version.json",
                        "2026-06-05T08:05:00Z",
                    ),
                )
                conn.execute(
                    """
                    INSERT INTO st_version_events (
                        st_version_event_id, source_row_id, unified_app_id, event_name,
                        estimated_start_date, estimated_end_date, event_description, source_refs
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        "stver_001",
                        "stver_src_001",
                        "57955d280211a6718a000002",
                        "android Version Update 1.2.3",
                        "2026-06-05",
                        "2026-06-05",
                        "Standalone version update.",
                        "[]",
                    ),
                )
                conn.commit()

                client = FakeFbEventClient()
                stats = build_unified_events_with_llm_merge(
                    conn,
                    client=client,
                    unified_app_id="57955d280211a6718a000002",
                    month="2026-06",
                )
                self.assertEqual(stats.merge_scopes, 1)
                self.assertEqual(stats.source_rows, 3)
                self.assertEqual(stats.merged_events, 2)

                unified_events = conn.execute(
                    """
                    SELECT canonical_event_name, anchor_source_type, event_category
                    FROM unified_events
                    ORDER BY canonical_event_name
                    """
                ).fetchall()
                self.assertEqual(
                    [
                        (row["canonical_event_name"], row["anchor_source_type"], row["event_category"])
                        for row in unified_events
                    ],
                    [
                        ("Summer Cup Minigame", "st_app_update_event", "Community Participation"),
                        ("android Version Update 1.2.3", "st_version_event", "Other"),
                    ],
                )

                source_count = conn.execute("SELECT COUNT(*) FROM unified_event_sources").fetchone()[0]
                self.assertEqual(source_count, 3)

                run_row = conn.execute(
                    """
                    SELECT status, merged_event_count, llm_input_tokens, llm_output_tokens, llm_total_cost_usd, session_id
                    FROM unified_event_merge_runs
                    """
                ).fetchone()
                self.assertEqual(run_row["status"], "success")
                self.assertEqual(run_row["merged_event_count"], 2)
                self.assertGreater(run_row["llm_input_tokens"], 0)
                self.assertGreater(run_row["llm_output_tokens"], 0)
                self.assertGreater(run_row["llm_total_cost_usd"], 0.0)
                self.assertTrue(run_row["session_id"])

                usage_rows = conn.execute(
                    """
                    SELECT stage, model, prompt_version, input_tokens, output_tokens, total_cost_usd
                    FROM llm_usage_log
                    ORDER BY usage_id
                    """
                ).fetchall()
                self.assertEqual(len(usage_rows), 3)
                self.assertEqual(
                    [row["stage"] for row in usage_rows],
                    ["fb_detection", "unified_step3_merge", "unified_step5_consolidation"],
                )
            finally:
                conn.close()
        finally:
            shutil.rmtree(temp_root, ignore_errors=True)

    def test_build_unified_events_with_llm_merge_handles_duplicate_name_date_ids(self) -> None:
        temp_root = Path.cwd() / "_tmp_unified_events_duplicate_id_test"
        db_path = temp_root / "warehouse.db"
        shutil.rmtree(temp_root, ignore_errors=True)
        temp_root.mkdir(parents=True, exist_ok=True)

        class DuplicateNameClient(FakeFbEventClient):
            def merge_unified_event_sources(self, merge_payload: dict[str, object]) -> dict[str, object]:
                source_events = merge_payload["source_events"]  # type: ignore[index]
                source_ids = [str(item["source_id"]) for item in source_events]  # type: ignore[index]
                return {
                    "events": [
                        {
                            "canonical_event_name": "Same Name Event",
                            "event_category": "Community Participation",
                            "estimated_start_date": "2026-06-10",
                            "estimated_end_date": "2026-06-10",
                            "canonical_event_description": "First event.",
                            "anchor_source_type": "fb_post",
                            "source_ids": [source_ids[0]],
                            "merge_confidence": 0.90,
                        },
                        {
                            "canonical_event_name": "Same Name Event",
                            "event_category": "Community Participation",
                            "estimated_start_date": "2026-06-10",
                            "estimated_end_date": "2026-06-10",
                            "canonical_event_description": "Second event.",
                            "anchor_source_type": "fb_post",
                            "source_ids": [source_ids[1]],
                            "merge_confidence": 0.89,
                        },
                    ],
                    "discarded_source_ids": [],
                }

        try:
            conn = open_connection(db_path)
            try:
                init_db(conn)
                conn.execute(
                    """
                    INSERT INTO config_app_mapping (
                        unified_app_id, fb_page_id, app_name, is_active
                    ) VALUES (?, ?, ?, ?)
                    """,
                    ("57955d280211a6718a000002", "1722990624697290", "Mobile Legends: Bang Bang", 1),
                )
                conn.executemany(
                    """
                    INSERT INTO raw_fb_posts (
                        source_post_id, fb_page_id, channel_id, channel_name, post_type,
                        post_description, duration, link, publish_time, hashtag,
                        engagement, reaction, comment, share, view, source_file, ingested_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    [
                        (
                            "post_dup_001",
                            "1722990624697290",
                            "1722990624697290",
                            "Mobile Legends Game VN",
                            "photo",
                            "First duplicate-named event.",
                            "",
                            "https://example.com/post-dup-1",
                            "2026-06-10T08:00:00+07:00",
                            "",
                            "10",
                            "5",
                            "2",
                            "1",
                            "2",
                            "fb_posts/mlbb.csv",
                            "2026-06-10T09:00:00+07:00",
                        ),
                        (
                            "post_dup_002",
                            "1722990624697290",
                            "1722990624697290",
                            "Mobile Legends Game VN",
                            "photo",
                            "Second duplicate-named event.",
                            "",
                            "https://example.com/post-dup-2",
                            "2026-06-10T10:00:00+07:00",
                            "",
                            "8",
                            "4",
                            "1",
                            "1",
                            "2",
                            "fb_posts/mlbb.csv",
                            "2026-06-10T10:30:00+07:00",
                        ),
                    ],
                )
                conn.commit()

                stats = build_unified_events_with_llm_merge(
                    conn,
                    client=DuplicateNameClient(),
                    unified_app_id="57955d280211a6718a000002",
                    month="2026-06",
                )
                self.assertEqual(stats.merge_scopes, 1)
                self.assertEqual(stats.merged_events, 2)

                rows = conn.execute(
                    """
                    SELECT unified_event_id, canonical_event_name
                    FROM unified_events
                    ORDER BY unified_event_id
                    """
                ).fetchall()
                self.assertEqual(len(rows), 2)
                self.assertEqual(rows[0]["canonical_event_name"], "Same Name Event")
                self.assertEqual(rows[1]["canonical_event_name"], "Same Name Event")
                self.assertNotEqual(rows[0]["unified_event_id"], rows[1]["unified_event_id"])
            finally:
                conn.close()
        finally:
            shutil.rmtree(temp_root, ignore_errors=True)

    def test_unified_merge_discards_unmatched_weak_fb_posts(self) -> None:
        temp_root = Path.cwd() / "_tmp_unified_events_discard_fb_test"
        db_path = temp_root / "warehouse.db"
        shutil.rmtree(temp_root, ignore_errors=True)
        temp_root.mkdir(parents=True, exist_ok=True)

        class DiscardFbClient(FakeFbEventClient):
            def merge_unified_event_sources(self, merge_payload: dict[str, object]) -> dict[str, object]:
                source_events = merge_payload["source_events"]  # type: ignore[index]
                fb_ids = [
                    str(item["source_id"])
                    for item in source_events  # type: ignore[assignment]
                    if str(item["source_type"]) == "fb_post"  # type: ignore[index]
                ]
                return {"events": [], "discarded_source_ids": fb_ids}

        try:
            conn = open_connection(db_path)
            try:
                init_db(conn)
                conn.execute(
                    """
                    INSERT INTO config_app_mapping (
                        unified_app_id, fb_page_id, app_name, is_active
                    ) VALUES (?, ?, ?, ?)
                    """,
                    ("57955d280211a6718a000002", "1722990624697290", "Mobile Legends: Bang Bang", 1),
                )
                conn.execute(
                    """
                    INSERT INTO raw_fb_posts (
                        source_post_id, fb_page_id, channel_id, channel_name, post_type,
                        post_description, duration, link, publish_time, hashtag,
                        engagement, reaction, comment, share, view, source_file, ingested_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        "post_discard_001",
                        "1722990624697290",
                        "1722990624697290",
                        "Mobile Legends Game VN",
                        "photo",
                        "Join and comment for a chance to win diamonds in our community event.",
                        "",
                        "https://example.com/post-discard-1",
                        "2026-06-10T08:00:00+07:00",
                        "",
                        "10",
                        "5",
                        "2",
                        "1",
                        "2",
                        "fb_posts/mlbb.csv",
                        "2026-06-10T09:00:00+07:00",
                    ),
                )
                conn.commit()

                stats = build_unified_events_with_llm_merge(
                    conn,
                    client=DiscardFbClient(),
                    unified_app_id="57955d280211a6718a000002",
                    month="2026-06",
                )
                self.assertEqual(stats.merge_scopes, 1)
                self.assertEqual(stats.merged_events, 0)
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM unified_events").fetchone()[0], 0)
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM unified_event_sources").fetchone()[0], 0)
            finally:
                conn.close()
        finally:
            shutil.rmtree(temp_root, ignore_errors=True)

    def test_unified_merge_harvests_remaining_valid_fb_posts(self) -> None:
        temp_root = Path.cwd() / "_tmp_unified_events_harvest_fb_test"
        db_path = temp_root / "warehouse.db"
        shutil.rmtree(temp_root, ignore_errors=True)
        temp_root.mkdir(parents=True, exist_ok=True)

        class HarvestFbClient(FakeFbEventClient):
            def merge_unified_event_sources(self, merge_payload: dict[str, object]) -> dict[str, object]:
                source_events = merge_payload["source_events"]  # type: ignore[index]
                fb_ids = [
                    str(item["source_id"])
                    for item in source_events  # type: ignore[assignment]
                    if str(item["source_type"]) == "fb_post"  # type: ignore[index]
                ]
                return {"events": [], "discarded_source_ids": fb_ids}

            def harvest_remaining_fb_post_events(self, post_payload: dict[str, object]) -> dict[str, object]:
                return {
                    "post_id": str(post_payload["post_id"]),
                    "events": [
                        {
                            "event_name": "Rương Cổ Vũ M7",
                            "estimated_start_date": "2026-02-01",
                            "estimated_end_date": "2026-02-08",
                            "event_description": "Người chơi tham gia nâng cấp Vé để nhận thêm phần thưởng và skin Prime.",
                            "category": "progression_season_systems",
                            "confidence": 0.88,
                            "evidence": "Tham gia sự kiện để nâng cấp Vé và nhận thêm nhiều phần thưởng.",
                        }
                    ],
                }

        try:
            conn = open_connection(db_path)
            try:
                init_db(conn)
                conn.execute(
                    """
                    INSERT INTO config_app_mapping (
                        unified_app_id, fb_page_id, app_name, is_active
                    ) VALUES (?, ?, ?, ?)
                    """,
                    ("57955d280211a6718a000002", "1722990624697290", "Mobile Legends: Bang Bang", 1),
                )
                conn.execute(
                    """
                    INSERT INTO raw_fb_posts (
                        source_post_id, fb_page_id, channel_id, channel_name, post_type,
                        post_description, duration, link, publish_time, hashtag,
                        engagement, reaction, comment, share, view, source_file, ingested_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        "post_harvest_001",
                        "1722990624697290",
                        "1722990624697290",
                        "Mobile Legends Game VN",
                        "photo",
                        "Đếm ngược bắt đầu! Rương Cổ Vũ M7 kết thúc vào 8/2! Tham gia sự kiện để nâng cấp Vé và nhận thêm nhiều phần thưởng.",
                        "",
                        "https://example.com/post-harvest-1",
                        "2026-02-01T11:00:00+07:00",
                        "#MLBBM7",
                        "20",
                        "10",
                        "2",
                        "1",
                        "7",
                        "fb_posts/mlbb.csv",
                        "2026-02-01T12:00:00+07:00",
                    ),
                )
                conn.commit()

                stats = build_unified_events_with_llm_merge(
                    conn,
                    client=HarvestFbClient(),
                    unified_app_id="57955d280211a6718a000002",
                    month="2026-02",
                )
                self.assertEqual(stats.merge_scopes, 1)
                self.assertEqual(stats.merged_events, 1)

                row = conn.execute(
                    """
                    SELECT canonical_event_name, event_category, anchor_source_type, prompt_version
                    FROM unified_events
                    """
                ).fetchone()
                self.assertEqual(
                    (
                        row["canonical_event_name"],
                        row["event_category"],
                        row["anchor_source_type"],
                        row["prompt_version"],
                    ),
                    (
                        "Rương Cổ Vũ M7",
                        "Progression / Season Systems",
                        "fb_post",
                        "fb_remaining_event_harvest_v2",
                    ),
                )
                source_rows = conn.execute(
                    "SELECT source_type, source_id FROM unified_event_sources"
                ).fetchall()
                self.assertEqual(
                    [(item["source_type"], item["source_id"]) for item in source_rows],
                    [("fb_post", "post_harvest_001")],
                )
            finally:
                conn.close()
        finally:
            shutil.rmtree(temp_root, ignore_errors=True)

    def test_unified_merge_final_dedup_merges_exact_cross_step_duplicates(self) -> None:
        temp_root = Path.cwd() / "_tmp_unified_events_cross_step_exact_dedup_test"
        db_path = temp_root / "warehouse.db"
        shutil.rmtree(temp_root, ignore_errors=True)
        temp_root.mkdir(parents=True, exist_ok=True)

        class ExactCrossStepDedupClient(FakeFbEventClient):
            def merge_unified_event_sources(self, merge_payload: dict[str, object]) -> dict[str, object]:
                source_events = merge_payload["source_events"]  # type: ignore[index]
                fb_ids = [
                    str(item["source_id"])
                    for item in source_events  # type: ignore[assignment]
                    if str(item["source_type"]) == "fb_post"  # type: ignore[index]
                ]
                return {
                    "events": [
                        {
                            "canonical_event_name": "Golden Month",
                            "event_category": "Retention / Free Rewards",
                            "estimated_start_date": "2026-02-17",
                            "estimated_end_date": "2026-03-16",
                            "canonical_event_description": "Chuỗi sự kiện Golden Month với phần thưởng miễn phí.",
                            "anchor_source_type": "fb_post",
                            "source_ids": [fb_ids[0]],
                            "merge_confidence": 0.92,
                        }
                    ],
                    "discarded_source_ids": [fb_ids[1]],
                }

            def harvest_remaining_fb_post_events(self, post_payload: dict[str, object]) -> dict[str, object]:
                return {
                    "post_id": str(post_payload["post_id"]),
                    "events": [
                        {
                            "event_name": "Golden Month",
                            "estimated_start_date": "2026-02-17",
                            "estimated_end_date": "2026-03-16",
                            "event_description": "Chuỗi sự kiện Golden Month với phần thưởng miễn phí.",
                            "category": "retention_free_rewards",
                            "confidence": 0.86,
                            "evidence": "Golden Month bắt đầu vào 17/2.",
                        }
                    ],
                }

            def consolidate_unified_candidates(self, merge_payload: dict[str, object]) -> dict[str, object]:
                candidate_events = merge_payload["candidate_events"]  # type: ignore[index]
                source_ids = [str(item["source_id"]) for item in candidate_events]  # type: ignore[assignment]
                return {
                    "events": [
                        {
                            "canonical_event_name": "Golden Month",
                            "event_category": "Retention / Free Rewards",
                            "estimated_start_date": "2026-02-17",
                            "estimated_end_date": "2026-03-16",
                            "canonical_event_description": "Chuỗi sự kiện Golden Month với phần thưởng miễn phí.",
                            "anchor_source_type": "fb_post",
                            "source_ids": source_ids,
                            "merge_confidence": 0.95,
                        }
                    ]
                }

        try:
            conn = open_connection(db_path)
            try:
                init_db(conn)
                conn.execute(
                    """
                    INSERT INTO config_app_mapping (
                        unified_app_id, fb_page_id, app_name, is_active
                    ) VALUES (?, ?, ?, ?)
                    """,
                    ("57955d280211a6718a000002", "1722990624697290", "Mobile Legends: Bang Bang", 1),
                )
                conn.executemany(
                    """
                    INSERT INTO raw_fb_posts (
                        source_post_id, fb_page_id, channel_id, channel_name, post_type,
                        post_description, duration, link, publish_time, hashtag,
                        engagement, reaction, comment, share, view, source_file, ingested_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    [
                        (
                            "post_xdup_001",
                            "1722990624697290",
                            "1722990624697290",
                            "Mobile Legends Game VN",
                            "photo",
                            "Golden Month bắt đầu 17/2 với nhiều phần thưởng miễn phí.",
                            "",
                            "https://example.com/post-xdup-1",
                            "2026-02-10T08:00:00+07:00",
                            "#goldenmonth",
                            "10",
                            "5",
                            "1",
                            "1",
                            "3",
                            "fb_posts/mlbb.csv",
                            "2026-02-10T09:00:00+07:00",
                        ),
                        (
                            "post_xdup_002",
                            "1722990624697290",
                            "1722990624697290",
                            "Mobile Legends Game VN",
                            "photo",
                            "Đừng bỏ lỡ Golden Month từ 17/2 đến 16/3 với quà miễn phí.",
                            "",
                            "https://example.com/post-xdup-2",
                            "2026-02-11T08:00:00+07:00",
                            "#goldenmonth",
                            "9",
                            "4",
                            "1",
                            "1",
                            "3",
                            "fb_posts/mlbb.csv",
                            "2026-02-11T09:00:00+07:00",
                        ),
                    ],
                )
                conn.commit()

                stats = build_unified_events_with_llm_merge(
                    conn,
                    client=ExactCrossStepDedupClient(),
                    unified_app_id="57955d280211a6718a000002",
                    month="2026-02",
                )
                self.assertEqual(stats.merge_scopes, 1)
                self.assertEqual(stats.merged_events, 1)

                row = conn.execute(
                    """
                    SELECT canonical_event_name, prompt_version
                    FROM unified_events
                    """
                ).fetchone()
                self.assertEqual(
                    (row["canonical_event_name"], row["prompt_version"]),
                    ("Golden Month", "unified_cross_source_event_consolidation_v2"),
                )
                source_count = conn.execute("SELECT COUNT(*) FROM unified_event_sources").fetchone()[0]
                self.assertEqual(source_count, 2)
                debug_counts = {
                    "step3": conn.execute(
                        "SELECT COUNT(*) FROM unified_event_step3_candidates"
                    ).fetchone()[0],
                    "step3_sources": conn.execute(
                        "SELECT COUNT(*) FROM unified_event_step3_candidate_sources"
                    ).fetchone()[0],
                    "step4": conn.execute(
                        "SELECT COUNT(*) FROM unified_event_step4_harvest_candidates"
                    ).fetchone()[0],
                    "step4_sources": conn.execute(
                        "SELECT COUNT(*) FROM unified_event_step4_harvest_candidate_sources"
                    ).fetchone()[0],
                    "step5": conn.execute(
                        "SELECT COUNT(*) FROM unified_event_step5_final_candidates"
                    ).fetchone()[0],
                    "step5_sources": conn.execute(
                        "SELECT COUNT(*) FROM unified_event_step5_final_candidate_sources"
                    ).fetchone()[0],
                }
                self.assertEqual(
                    debug_counts,
                    {
                        "step3": 1,
                        "step3_sources": 1,
                        "step4": 1,
                        "step4_sources": 1,
                        "step5": 1,
                        "step5_sources": 2,
                    },
                )
            finally:
                conn.close()
        finally:
            shutil.rmtree(temp_root, ignore_errors=True)

    def test_unified_merge_final_dedup_prefers_umbrella_event_over_child_restatement(self) -> None:
        temp_root = Path.cwd() / "_tmp_unified_events_cross_step_umbrella_dedup_test"
        db_path = temp_root / "warehouse.db"
        shutil.rmtree(temp_root, ignore_errors=True)
        temp_root.mkdir(parents=True, exist_ok=True)

        class UmbrellaCrossStepDedupClient(FakeFbEventClient):
            def merge_unified_event_sources(self, merge_payload: dict[str, object]) -> dict[str, object]:
                source_events = merge_payload["source_events"]  # type: ignore[index]
                fb_ids = [
                    str(item["source_id"])
                    for item in source_events  # type: ignore[assignment]
                    if str(item["source_type"]) == "fb_post"  # type: ignore[index]
                ]
                return {
                    "events": [
                        {
                            "canonical_event_name": "Golden Month",
                            "event_category": "Retention / Free Rewards",
                            "estimated_start_date": "2026-02-17",
                            "estimated_end_date": "2026-03-16",
                            "canonical_event_description": "Chuỗi sự kiện Golden Month với nhiều quà miễn phí.",
                            "anchor_source_type": "fb_post",
                            "source_ids": [fb_ids[0]],
                            "merge_confidence": 0.91,
                        }
                    ],
                    "discarded_source_ids": [fb_ids[1]],
                }

            def harvest_remaining_fb_post_events(self, post_payload: dict[str, object]) -> dict[str, object]:
                return {
                    "post_id": str(post_payload["post_id"]),
                    "events": [
                        {
                            "event_name": "Nhận Skin Ngẫu Nhiên Dễ Dàng",
                            "estimated_start_date": "2026-02-17",
                            "estimated_end_date": "2026-03-23",
                            "event_description": "Đăng nhập để nhận miễn phí 1 trong 5 skin Elite trong Golden Month.",
                            "category": "retention_free_rewards",
                            "confidence": 0.83,
                            "evidence": "Đăng nhập game để nhận miễn phí 1 trong 5 skin Elite.",
                        }
                    ],
                }

            def consolidate_unified_candidates(self, merge_payload: dict[str, object]) -> dict[str, object]:
                candidate_events = merge_payload["candidate_events"]  # type: ignore[index]
                source_ids = [str(item["source_id"]) for item in candidate_events]  # type: ignore[assignment]
                return {
                    "events": [
                        {
                            "canonical_event_name": "Golden Month",
                            "event_category": "Retention / Free Rewards",
                            "estimated_start_date": "2026-02-17",
                            "estimated_end_date": "2026-03-23",
                            "canonical_event_description": "Chuỗi sự kiện Golden Month với nhiều quà miễn phí, bao gồm hoạt động nhận skin ngẫu nhiên.",
                            "anchor_source_type": "fb_post",
                            "source_ids": source_ids,
                            "merge_confidence": 0.93,
                        }
                    ]
                }

        try:
            conn = open_connection(db_path)
            try:
                init_db(conn)
                conn.execute(
                    """
                    INSERT INTO config_app_mapping (
                        unified_app_id, fb_page_id, app_name, is_active
                    ) VALUES (?, ?, ?, ?)
                    """,
                    ("57955d280211a6718a000002", "1722990624697290", "Mobile Legends: Bang Bang", 1),
                )
                conn.executemany(
                    """
                    INSERT INTO raw_fb_posts (
                        source_post_id, fb_page_id, channel_id, channel_name, post_type,
                        post_description, duration, link, publish_time, hashtag,
                        engagement, reaction, comment, share, view, source_file, ingested_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    [
                        (
                            "post_umb_001",
                            "1722990624697290",
                            "1722990624697290",
                            "Mobile Legends Game VN",
                            "photo",
                            "Golden Month khởi động từ 17/2 với nhiều quà miễn phí và hoạt động nhận skin.",
                            "",
                            "https://example.com/post-umb-1",
                            "2026-02-10T08:00:00+07:00",
                            "#goldenmonth",
                            "10",
                            "5",
                            "1",
                            "1",
                            "3",
                            "fb_posts/mlbb.csv",
                            "2026-02-10T09:00:00+07:00",
                        ),
                        (
                            "post_umb_002",
                            "1722990624697290",
                            "1722990624697290",
                            "Mobile Legends Game VN",
                            "photo",
                            "Đăng nhập game để nhận miễn phí 1 trong 5 skin Elite trong Golden Month.",
                            "",
                            "https://example.com/post-umb-2",
                            "2026-02-17T08:00:00+07:00",
                            "#goldenmonth",
                            "9",
                            "4",
                            "1",
                            "1",
                            "3",
                            "fb_posts/mlbb.csv",
                            "2026-02-17T09:00:00+07:00",
                        ),
                    ],
                )
                conn.commit()

                stats = build_unified_events_with_llm_merge(
                    conn,
                    client=UmbrellaCrossStepDedupClient(),
                    unified_app_id="57955d280211a6718a000002",
                    month="2026-02",
                )
                self.assertEqual(stats.merge_scopes, 1)
                self.assertEqual(stats.merged_events, 1)

                row = conn.execute(
                    """
                    SELECT canonical_event_name, prompt_version
                    FROM unified_events
                    """
                ).fetchone()
                self.assertEqual(
                    (row["canonical_event_name"], row["prompt_version"]),
                    ("Golden Month", "unified_cross_source_event_consolidation_v2"),
                )
                source_rows = conn.execute(
                    "SELECT source_id FROM unified_event_sources ORDER BY source_id"
                ).fetchall()
                self.assertEqual(
                    [item["source_id"] for item in source_rows],
                    ["post_umb_001", "post_umb_002"],
                )
            finally:
                conn.close()
        finally:
            shutil.rmtree(temp_root, ignore_errors=True)

    def test_rerun_unified_step5_reuses_saved_snapshots_and_merges_alias_names(self) -> None:
        temp_root = Path.cwd() / "_tmp_unified_step5_rerun_alias_test"
        db_path = temp_root / "warehouse.db"
        shutil.rmtree(temp_root, ignore_errors=True)
        temp_root.mkdir(parents=True, exist_ok=True)

        class Step5OnlyGuardClient(FakeFbEventClient):
            def detect_post_event(self, post_payload: dict[str, object]) -> dict[str, object]:
                raise AssertionError("step5 rerun should not call detection")

            def merge_unified_event_sources(self, merge_payload: dict[str, object]) -> dict[str, object]:
                raise AssertionError("step5 rerun should not call step 3 merge")

            def harvest_remaining_fb_post_events(self, post_payload: dict[str, object]) -> dict[str, object]:
                raise AssertionError("step5 rerun should not call step 4 harvest")

            def consolidate_unified_candidates(self, merge_payload: dict[str, object]) -> dict[str, object]:
                raise AssertionError("deterministic alias prebucketing should merge this case before the LLM")

        try:
            conn = open_connection(db_path)
            try:
                init_db(conn)
                conn.execute(
                    """
                    INSERT INTO config_app_mapping (
                        unified_app_id, fb_page_id, app_name, is_active
                    ) VALUES (?, ?, ?, ?)
                    """,
                    ("57955d280211a6718a000002", "1722990624697290", "Mobile Legends: Bang Bang", 1),
                )
                conn.executemany(
                    """
                    INSERT INTO raw_fb_posts (
                        source_post_id, unified_app_id, fb_page_id, channel_id, channel_name, post_type,
                        post_description, duration, link, publish_time, hashtag,
                        engagement, reaction, comment, share, view, source_file, ingested_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    [
                        (
                            "post_marcel_001",
                            "57955d280211a6718a000002",
                            "1722990624697290",
                            "1722990624697290",
                            "Mobile Legends Game VN",
                            "photo",
                            "Marcel ra mắt - Bắt trọn khoảnh khắc để nhận quà cộng đồng.",
                            "",
                            "https://example.com/post-marcel-1",
                            "2026-03-20T08:00:00+07:00",
                            "#marcel",
                            "12",
                            "6",
                            "1",
                            "1",
                            "3",
                            "fb_posts/mlbb.csv",
                            "2026-03-20T09:00:00+07:00",
                        ),
                        (
                            "post_marcel_002",
                            "57955d280211a6718a000002",
                            "1722990624697290",
                            "1722990624697290",
                            "Mobile Legends Game VN",
                            "photo",
                            "Nắm bắt khoảnh khắc Marcel để tham gia bảng xếp hạng cộng đồng.",
                            "",
                            "https://example.com/post-marcel-2",
                            "2026-03-24T08:00:00+07:00",
                            "#marcel",
                            "15",
                            "7",
                            "2",
                            "1",
                            "4",
                            "fb_posts/mlbb.csv",
                            "2026-03-24T09:00:00+07:00",
                        ),
                    ],
                )
                conn.execute(
                    """
                    INSERT INTO unified_event_merge_runs (
                        run_id, unified_app_id, month_bucket, source_row_count, merged_event_count,
                        model, prompt_version, build_mode, source_snapshot_run_id, started_at, finished_at, status
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        "uemrun_source_001",
                        "57955d280211a6718a000002",
                        "2026-04",
                        2,
                        0,
                        "gpt-5.4",
                        "unified_cross_source_event_merge_v4",
                        "full",
                        None,
                        "2026-06-11T10:00:00+00:00",
                        "2026-06-11T10:05:00+00:00",
                        "success",
                    ),
                )
                conn.executemany(
                    """
                    INSERT INTO unified_event_step3_candidates (
                        run_id, unified_app_id, month_bucket, candidate_id, canonical_event_name,
                        event_category, estimated_start_date, estimated_end_date,
                        canonical_event_description, anchor_source_type, merge_confidence,
                        merge_model, prompt_version, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    [
                        (
                            "uemrun_source_001",
                            "57955d280211a6718a000002",
                            "2026-04",
                            "candidate_0",
                            "Marcel ra mắt - Bắt trọn khoảnh khắc",
                            "Community Participation",
                            "2026-03-20",
                            "2026-03-25",
                            "Sự kiện cộng đồng ra mắt Marcel với phần thưởng và bảng xếp hạng.",
                            "fb_post",
                            0.88,
                            "gpt-5.4",
                            "unified_cross_source_event_merge_v4",
                            "2026-06-11T10:00:00+00:00",
                        ),
                        (
                            "uemrun_source_001",
                            "57955d280211a6718a000002",
                            "2026-04",
                            "candidate_1",
                            "Nắm bắt khoảnh khắc Marcel",
                            "Community Participation",
                            "2026-03-21",
                            "2026-03-25",
                            "Sự kiện cộng đồng ghi lại khoảnh khắc Marcel để tranh bảng xếp hạng.",
                            "fb_post",
                            0.86,
                            "gpt-5.4",
                            "unified_cross_source_event_merge_v4",
                            "2026-06-11T10:00:00+00:00",
                        ),
                    ],
                )
                conn.executemany(
                    """
                    INSERT INTO unified_event_step3_candidate_sources (
                        run_id, candidate_id, source_type, source_id, source_time, source_post_id, source_confidence
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    [
                        (
                            "uemrun_source_001",
                            "candidate_0",
                            "fb_post",
                            "post_marcel_001",
                            "2026-03-20T08:00:00+07:00",
                            "post_marcel_001",
                            1.0,
                        ),
                        (
                            "uemrun_source_001",
                            "candidate_1",
                            "fb_post",
                            "post_marcel_002",
                            "2026-03-24T08:00:00+07:00",
                            "post_marcel_002",
                            1.0,
                        ),
                    ],
                )
                conn.commit()

                stats = rerun_unified_step5(
                    conn,
                    client=Step5OnlyGuardClient(),
                    unified_app_id="57955d280211a6718a000002",
                    month="2026-04",
                    source_run_id="uemrun_source_001",
                )
                self.assertEqual(stats.merge_scopes, 1)
                self.assertEqual(stats.source_rows, 2)
                self.assertEqual(stats.merged_events, 1)
                self.assertEqual(
                    conn.execute("SELECT COUNT(*) FROM unified_event_step3_candidates").fetchone()[0],
                    2,
                )
                self.assertEqual(
                    conn.execute("SELECT COUNT(*) FROM unified_event_step5_final_candidates").fetchone()[0],
                    1,
                )
                self.assertEqual(
                    conn.execute("SELECT COUNT(*) FROM unified_event_step5_final_candidate_sources").fetchone()[0],
                    2,
                )
                final_event = conn.execute(
                    """
                    SELECT canonical_event_name, event_category, prompt_version
                    FROM unified_events
                    """
                ).fetchone()
                self.assertEqual(final_event["event_category"], "Community Participation")
                self.assertEqual(final_event["prompt_version"], "unified_cross_source_event_consolidation_v2")
                final_sources = conn.execute(
                    "SELECT source_id FROM unified_event_sources ORDER BY source_id"
                ).fetchall()
                self.assertEqual(
                    [row["source_id"] for row in final_sources],
                    ["post_marcel_001", "post_marcel_002"],
                )
                latest_run = conn.execute(
                    """
                    SELECT build_mode, source_snapshot_run_id, status
                    FROM unified_event_merge_runs
                    WHERE build_mode = 'step5_only'
                    LIMIT 1
                    """
                ).fetchone()
                self.assertEqual(
                    (
                        latest_run["build_mode"],
                        latest_run["source_snapshot_run_id"],
                        latest_run["status"],
                    ),
                    ("step5_only", "uemrun_source_001", "success"),
                )
            finally:
                conn.close()
        finally:
            shutil.rmtree(temp_root, ignore_errors=True)

    def test_unified_merge_prebucket_collapses_same_name_overlapping_draw_candidates(self) -> None:
        temp_root = Path.cwd() / "_tmp_unified_events_prebucket_overlap_test"
        db_path = temp_root / "warehouse.db"
        shutil.rmtree(temp_root, ignore_errors=True)
        temp_root.mkdir(parents=True, exist_ok=True)

        class PrebucketOverlapClient(FakeFbEventClient):
            def merge_unified_event_sources(self, merge_payload: dict[str, object]) -> dict[str, object]:
                source_events = merge_payload["source_events"]  # type: ignore[index]
                fb_ids = [
                    str(item["source_id"])
                    for item in source_events  # type: ignore[assignment]
                    if str(item["source_type"]) == "fb_post"  # type: ignore[index]
                ]
                return {
                    "events": [
                        {
                            "canonical_event_name": "Vòng Quay Lấp Lánh",
                            "event_category": "Monetization",
                            "estimated_start_date": "2026-02-17",
                            "estimated_end_date": "2026-04-02",
                            "canonical_event_description": "Sự kiện quay nhận skin Hanabi.",
                            "anchor_source_type": "fb_post",
                            "source_ids": [fb_ids[0]],
                            "merge_confidence": 0.9,
                        }
                    ],
                    "discarded_source_ids": [fb_ids[1]],
                }

            def harvest_remaining_fb_post_events(self, post_payload: dict[str, object]) -> dict[str, object]:
                return {
                    "post_id": str(post_payload["post_id"]),
                    "events": [
                        {
                            "event_name": "Vòng Quay Lấp Lánh",
                            "estimated_start_date": "2026-02-18",
                            "estimated_end_date": None,
                            "event_description": "Tham gia vòng quay để nhận Cảm Xúc Trận Đấu miễn phí.",
                            "category": "monetization",
                            "confidence": 0.81,
                            "evidence": "Tham gia vòng quay để nhận Cảm Xúc Trận Đấu miễn phí.",
                        }
                    ],
                }

            def consolidate_unified_candidates(self, merge_payload: dict[str, object]) -> dict[str, object]:
                candidate_events = merge_payload["candidate_events"]  # type: ignore[index]
                return {
                    "events": [
                        {
                            "canonical_event_name": str(item["event_name"]),
                            "event_category": str(item["event_category"]),
                            "estimated_start_date": item["estimated_start_date"],
                            "estimated_end_date": item["estimated_end_date"],
                            "canonical_event_description": str(item["event_description"]),
                            "anchor_source_type": str(item["source_type"]),
                            "source_ids": [str(item["source_id"])],
                            "merge_confidence": float(item["source_confidence"]),
                        }
                        for item in candidate_events  # type: ignore[assignment]
                    ]
                }

        try:
            conn = open_connection(db_path)
            try:
                init_db(conn)
                conn.execute(
                    """
                    INSERT INTO config_app_mapping (
                        unified_app_id, fb_page_id, app_name, is_active
                    ) VALUES (?, ?, ?, ?)
                    """,
                    ("57955d280211a6718a000002", "1722990624697290", "Mobile Legends: Bang Bang", 1),
                )
                conn.executemany(
                    """
                    INSERT INTO raw_fb_posts (
                        source_post_id, fb_page_id, channel_id, channel_name, post_type,
                        post_description, duration, link, publish_time, hashtag,
                        engagement, reaction, comment, share, view, source_file, ingested_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    [
                        (
                            "post_prebucket_001",
                            "1722990624697290",
                            "1722990624697290",
                            "Mobile Legends Game VN",
                            "photo",
                            "Vòng Quay Lấp Lánh bắt đầu 17/2 để nhận skin Hanabi.",
                            "",
                            "https://example.com/post-prebucket-1",
                            "2026-02-17T08:00:00+07:00",
                            "#goldenmonth",
                            "10",
                            "5",
                            "1",
                            "1",
                            "3",
                            "fb_posts/mlbb.csv",
                            "2026-02-17T09:00:00+07:00",
                        ),
                        (
                            "post_prebucket_002",
                            "1722990624697290",
                            "1722990624697290",
                            "Mobile Legends Game VN",
                            "photo",
                            "Tham gia Vòng Quay Lấp Lánh để nhận Cảm Xúc Trận Đấu miễn phí.",
                            "",
                            "https://example.com/post-prebucket-2",
                            "2026-02-18T08:00:00+07:00",
                            "#goldenmonth",
                            "9",
                            "4",
                            "1",
                            "1",
                            "3",
                            "fb_posts/mlbb.csv",
                            "2026-02-18T09:00:00+07:00",
                        ),
                    ],
                )
                conn.commit()

                stats = build_unified_events_with_llm_merge(
                    conn,
                    client=PrebucketOverlapClient(),
                    unified_app_id="57955d280211a6718a000002",
                    month="2026-02",
                )
                self.assertEqual(stats.merge_scopes, 1)
                self.assertEqual(stats.merged_events, 1)
                source_ids = conn.execute(
                    "SELECT source_id FROM unified_event_sources ORDER BY source_id"
                ).fetchall()
                self.assertEqual(
                    [item["source_id"] for item in source_ids],
                    ["post_prebucket_001", "post_prebucket_002"],
                )
            finally:
                conn.close()
        finally:
            shutil.rmtree(temp_root, ignore_errors=True)

    def test_unified_merge_prebucket_keeps_far_apart_same_name_draw_candidates_separate(self) -> None:
        temp_root = Path.cwd() / "_tmp_unified_events_prebucket_far_apart_test"
        db_path = temp_root / "warehouse.db"
        shutil.rmtree(temp_root, ignore_errors=True)
        temp_root.mkdir(parents=True, exist_ok=True)

        class PrebucketFarApartClient(FakeFbEventClient):
            def merge_unified_event_sources(self, merge_payload: dict[str, object]) -> dict[str, object]:
                source_events = merge_payload["source_events"]  # type: ignore[index]
                fb_ids = [
                    str(item["source_id"])
                    for item in source_events  # type: ignore[assignment]
                    if str(item["source_type"]) == "fb_post"  # type: ignore[index]
                ]
                return {
                    "events": [
                        {
                            "canonical_event_name": "Rương Ánh Sao",
                            "event_category": "Monetization",
                            "estimated_start_date": "2026-01-22",
                            "estimated_end_date": None,
                            "canonical_event_description": "Mở rương với giá 10 Kim Cương.",
                            "anchor_source_type": "fb_post",
                            "source_ids": [fb_ids[0]],
                            "merge_confidence": 0.9,
                        }
                    ],
                    "discarded_source_ids": [fb_ids[1]],
                }

            def harvest_remaining_fb_post_events(self, post_payload: dict[str, object]) -> dict[str, object]:
                return {
                    "post_id": str(post_payload["post_id"]),
                    "events": [
                        {
                            "event_name": "Rương Ánh Sao",
                            "estimated_start_date": "2026-02-22",
                            "estimated_end_date": None,
                            "event_description": "Mở rương tháng này để nhận Thẻ Ánh Sao.",
                            "category": "monetization",
                            "confidence": 0.82,
                            "evidence": "Mở rương tháng này để nhận Thẻ Ánh Sao.",
                        }
                    ],
                }

            def consolidate_unified_candidates(self, merge_payload: dict[str, object]) -> dict[str, object]:
                candidate_events = merge_payload["candidate_events"]  # type: ignore[index]
                return {
                    "events": [
                        {
                            "canonical_event_name": str(item["event_name"]),
                            "event_category": str(item["event_category"]),
                            "estimated_start_date": item["estimated_start_date"],
                            "estimated_end_date": item["estimated_end_date"],
                            "canonical_event_description": str(item["event_description"]),
                            "anchor_source_type": str(item["source_type"]),
                            "source_ids": [str(item["source_id"])],
                            "merge_confidence": float(item["source_confidence"]),
                        }
                        for item in candidate_events  # type: ignore[assignment]
                    ]
                }

        try:
            conn = open_connection(db_path)
            try:
                init_db(conn)
                conn.execute(
                    """
                    INSERT INTO config_app_mapping (
                        unified_app_id, fb_page_id, app_name, is_active
                    ) VALUES (?, ?, ?, ?)
                    """,
                    ("57955d280211a6718a000002", "1722990624697290", "Mobile Legends: Bang Bang", 1),
                )
                conn.executemany(
                    """
                    INSERT INTO raw_fb_posts (
                        source_post_id, fb_page_id, channel_id, channel_name, post_type,
                        post_description, duration, link, publish_time, hashtag,
                        engagement, reaction, comment, share, view, source_file, ingested_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    [
                        (
                            "post_far_001",
                            "1722990624697290",
                            "1722990624697290",
                            "Mobile Legends Game VN",
                            "photo",
                            "Rương Ánh Sao mở từ 22/1 để nhận Thẻ Ánh Sao.",
                            "",
                            "https://example.com/post-far-1",
                            "2026-01-22T08:00:00+07:00",
                            "#starlight",
                            "10",
                            "5",
                            "1",
                            "1",
                            "3",
                            "fb_posts/mlbb.csv",
                            "2026-01-22T09:00:00+07:00",
                        ),
                        (
                            "post_far_002",
                            "1722990624697290",
                            "1722990624697290",
                            "Mobile Legends Game VN",
                            "photo",
                            "Rương Ánh Sao tháng này mở từ 22/2 để nhận Thẻ Ánh Sao.",
                            "",
                            "https://example.com/post-far-2",
                            "2026-02-22T08:00:00+07:00",
                            "#starlight",
                            "9",
                            "4",
                            "1",
                            "1",
                            "3",
                            "fb_posts/mlbb.csv",
                            "2026-02-22T09:00:00+07:00",
                        ),
                    ],
                )
                conn.commit()

                stats = build_unified_events_with_llm_merge(
                    conn,
                    client=PrebucketFarApartClient(),
                    unified_app_id="57955d280211a6718a000002",
                    month="2026-02",
                )
                self.assertEqual(stats.merge_scopes, 1)
                self.assertEqual(stats.merged_events, 2)
            finally:
                conn.close()
        finally:
            shutil.rmtree(temp_root, ignore_errors=True)

    def test_rerun_unified_step5_merges_store_offer_aliases_with_description_support(self) -> None:
        temp_root = Path.cwd() / "_tmp_unified_step5_rerun_description_merge_test"
        db_path = temp_root / "warehouse.db"
        shutil.rmtree(temp_root, ignore_errors=True)
        temp_root.mkdir(parents=True, exist_ok=True)

        class Step5DescriptionGuardClient(FakeFbEventClient):
            def detect_post_event(self, post_payload: dict[str, object]) -> dict[str, object]:
                raise AssertionError("step5 rerun should not call detection")

            def merge_unified_event_sources(self, merge_payload: dict[str, object]) -> dict[str, object]:
                raise AssertionError("step5 rerun should not call step 3 merge")

            def harvest_remaining_fb_post_events(self, post_payload: dict[str, object]) -> dict[str, object]:
                raise AssertionError("step5 rerun should not call step 4 harvest")

            def consolidate_unified_candidates(self, merge_payload: dict[str, object]) -> dict[str, object]:
                raise AssertionError("deterministic step-5 prebucketing should merge this case before the LLM")

        try:
            conn = open_connection(db_path)
            try:
                init_db(conn)
                conn.execute(
                    """
                    INSERT INTO config_app_mapping (
                        unified_app_id, fb_page_id, app_name, is_active
                    ) VALUES (?, ?, ?, ?)
                    """,
                    ("57955d280211a6718a000002", "1722990624697290", "Mobile Legends: Bang Bang", 1),
                )
                conn.executemany(
                    """
                    INSERT INTO raw_fb_posts (
                        source_post_id, unified_app_id, fb_page_id, channel_id, channel_name, post_type,
                        post_description, duration, link, publish_time, hashtag,
                        engagement, reaction, comment, share, view, source_file, ingested_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    [
                        (
                            "post_franco_001",
                            "57955d280211a6718a000002",
                            "1722990624697290",
                            "1722990624697290",
                            "Mobile Legends Game VN",
                            "photo",
                            "Ưu đãi giảm 20% tuần đầu cho Franco Đệ Lục Ma Vương tại Cửa Hàng.",
                            "",
                            "https://example.com/post-franco-1",
                            "2026-04-12T08:00:00+07:00",
                            "#franco",
                            "10",
                            "5",
                            "1",
                            "1",
                            "2",
                            "fb_posts/mlbb.csv",
                            "2026-04-12T09:00:00+07:00",
                        ),
                        (
                            "post_franco_002",
                            "57955d280211a6718a000002",
                            "1722990624697290",
                            "1722990624697290",
                            "Mobile Legends Game VN",
                            "photo",
                            "Ưu đãi tuần đầu Franco Đệ Lục Ma Vương tại Cửa Hàng với cùng cơ chế giảm giá mở bán.",
                            "",
                            "https://example.com/post-franco-2",
                            "2026-04-12T12:00:00+07:00",
                            "#franco",
                            "11",
                            "5",
                            "1",
                            "1",
                            "2",
                            "fb_posts/mlbb.csv",
                            "2026-04-12T12:30:00+07:00",
                        ),
                    ],
                )
                conn.execute(
                    """
                    INSERT INTO unified_event_merge_runs (
                        run_id, unified_app_id, month_bucket, source_row_count, merged_event_count,
                        model, prompt_version, build_mode, source_snapshot_run_id, started_at, finished_at, status
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        "uemrun_source_franco_001",
                        "57955d280211a6718a000002",
                        "2026-04",
                        2,
                        0,
                        "gpt-5.4",
                        "unified_cross_source_event_merge_v4",
                        "full",
                        None,
                        "2026-06-11T11:00:00+00:00",
                        "2026-06-11T11:05:00+00:00",
                        "success",
                    ),
                )
                conn.executemany(
                    """
                    INSERT INTO unified_event_step3_candidates (
                        run_id, unified_app_id, month_bucket, candidate_id, canonical_event_name,
                        event_category, estimated_start_date, estimated_end_date,
                        canonical_event_description, anchor_source_type, merge_confidence,
                        merge_model, prompt_version, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    [
                        (
                            "uemrun_source_franco_001",
                            "57955d280211a6718a000002",
                            "2026-04",
                            "candidate_0",
                            "Ưu đãi giảm 20% tuần đầu cho Franco \"Đệ Lục Ma Vương\" tại Cửa Hàng",
                            "Monetization",
                            "2026-04-12",
                            None,
                            "Cửa hàng mở bán Chân Dung Sống Động Franco với ưu đãi giảm 20% trong tuần đầu.",
                            "fb_post",
                            0.88,
                            "gpt-5.4",
                            "unified_cross_source_event_merge_v4",
                            "2026-06-11T11:00:00+00:00",
                        ),
                        (
                            "uemrun_source_franco_001",
                            "57955d280211a6718a000002",
                            "2026-04",
                            "candidate_1",
                            "Ưu đãi tuần đầu Franco \"Đệ Lục Ma Vương\" tại Cửa Hàng",
                            "Monetization",
                            "2026-04-12",
                            None,
                            "Cửa hàng mở bán Chân Dung Sống Động Franco trong tuần đầu với cùng cơ chế ưu đãi giảm giá.",
                            "fb_post",
                            0.86,
                            "gpt-5.4",
                            "unified_cross_source_event_merge_v4",
                            "2026-06-11T11:00:00+00:00",
                        ),
                    ],
                )
                conn.executemany(
                    """
                    INSERT INTO unified_event_step3_candidate_sources (
                        run_id, candidate_id, source_type, source_id, source_time, source_post_id, source_confidence
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    [
                        (
                            "uemrun_source_franco_001",
                            "candidate_0",
                            "fb_post",
                            "post_franco_001",
                            "2026-04-12T08:00:00+07:00",
                            "post_franco_001",
                            1.0,
                        ),
                        (
                            "uemrun_source_franco_001",
                            "candidate_1",
                            "fb_post",
                            "post_franco_002",
                            "2026-04-12T12:00:00+07:00",
                            "post_franco_002",
                            1.0,
                        ),
                    ],
                )
                conn.commit()

                stats = rerun_unified_step5(
                    conn,
                    client=Step5DescriptionGuardClient(),
                    unified_app_id="57955d280211a6718a000002",
                    month="2026-04",
                    source_run_id="uemrun_source_franco_001",
                )
                self.assertEqual(stats.merged_events, 1)
                final_sources = conn.execute(
                    "SELECT source_id FROM unified_event_sources ORDER BY source_id"
                ).fetchall()
                self.assertEqual(
                    [row["source_id"] for row in final_sources],
                    ["post_franco_001", "post_franco_002"],
                )
            finally:
                conn.close()
        finally:
            shutil.rmtree(temp_root, ignore_errors=True)

    def test_unified_event_id_differs_across_month_buckets(self) -> None:
        from vn_event_dw.fb_event_pipeline import _unified_event_id

        january_id = _unified_event_id(
            unified_app_id="57955d280211a6718a000002",
            canonical_event_name="Summer Cup Minigame",
            estimated_start_date="2026-02-01",
            month_bucket="2026-01",
        )
        february_id = _unified_event_id(
            unified_app_id="57955d280211a6718a000002",
            canonical_event_name="Summer Cup Minigame",
            estimated_start_date="2026-02-01",
            month_bucket="2026-02",
        )

        self.assertNotEqual(january_id, february_id)


if __name__ == "__main__":
    unittest.main()
