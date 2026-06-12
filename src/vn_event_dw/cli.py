from __future__ import annotations

import argparse
import os
from datetime import date
from pathlib import Path

import uvicorn

from .api import create_app
from .config import default_sensortower_raw_dir, load_pipeline_config
from .etl import open_connection, init_db, reload_fb_posts, run_etl, summarize_db
from .environment import load_environment_files
from .fb_event_pipeline import (
    build_fb_event_detection,
    build_fb_event_objects,
    build_fb_events,
    build_fb_events_with_llm_merge,
    build_fb_raw_events,
    build_unified_events_with_llm_merge,
    preview_fb_event_dedup,
    rerun_unified_step5,
)
from .ngrok_service import serve_api_with_ngrok
from .sensortower_raw import (
    SensorTowerClient,
    extract_sensortower_raw,
    load_pending_sensortower_raw_manifests,
    load_sensortower_raw_manifest,
    resolve_raw_window,
    resolve_tracked_targets,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="vn-event-dw", description="Mini event data warehouse ETL")
    subparsers = parser.add_subparsers(dest="command", required=True)

    init_parser = subparsers.add_parser("init-db", help="Create the warehouse schema")
    init_parser.add_argument("--db", required=True, type=Path, help="SQLite database path")

    run_parser = subparsers.add_parser("run", help="Run the full ETL pipeline")
    run_parser.add_argument("--db", required=True, type=Path, help="SQLite database path")
    run_parser.add_argument("--config", required=True, type=Path, help="JSON config path")
    run_parser.add_argument("--input-dir", required=True, type=Path, help="Directory with CSV inputs")

    reload_fb_parser = subparsers.add_parser(
        "reload-fb-posts",
        help="Reload only the FB post landing tables from the FB CSV folder.",
    )
    reload_fb_parser.add_argument("--db", required=True, type=Path, help="SQLite database path")
    reload_fb_parser.add_argument("--config", required=True, type=Path, help="JSON config path")
    reload_fb_parser.add_argument(
        "--input-dir",
        required=True,
        type=Path,
        help="Directory containing the fb_posts folder or legacy fb_posts.csv.",
    )

    detect_fb_parser = subparsers.add_parser(
        "build-fb-event-detection",
        help="Run LLM binary event detection for Facebook posts.",
    )
    detect_fb_parser.add_argument("--db", required=True, type=Path, help="SQLite database path")
    detect_fb_parser.add_argument("--fb-page-id", default=None, help="Optional FB page id scope.")
    detect_fb_parser.add_argument("--game-name", default=None, help="Optional canonical game name scope.")
    detect_fb_parser.add_argument("--page-name", default=None, help="Optional page name scope.")
    detect_fb_parser.add_argument("--limit", type=int, default=None, help="Optional max posts to process.")

    extract_fb_parser = subparsers.add_parser(
        "build-fb-event-objects",
        help="Run LLM event object extraction for detected Facebook event posts.",
    )
    extract_fb_parser.add_argument("--db", required=True, type=Path, help="SQLite database path")
    extract_fb_parser.add_argument("--fb-page-id", default=None, help="Optional FB page id scope.")
    extract_fb_parser.add_argument("--game-name", default=None, help="Optional canonical game name scope.")
    extract_fb_parser.add_argument("--page-name", default=None, help="Optional page name scope.")
    extract_fb_parser.add_argument("--limit", type=int, default=None, help="Optional max posts to process.")

    raw_events_fb_parser = subparsers.add_parser(
        "build-fb-raw-events",
        help="Run FB detection + extraction only, producing non-deduplicated raw events.",
    )
    raw_events_fb_parser.add_argument("--db", required=True, type=Path, help="SQLite database path")
    raw_events_fb_parser.add_argument("--fb-page-id", default=None, help="Optional FB page id scope.")
    raw_events_fb_parser.add_argument("--game-name", default=None, help="Optional canonical game name scope.")
    raw_events_fb_parser.add_argument("--page-name", default=None, help="Optional page name scope.")
    raw_events_fb_parser.add_argument("--limit", type=int, default=None, help="Optional max posts to process.")

    dedup_fb_parser = subparsers.add_parser(
        "build-fb-events",
        help="Deduplicate extracted Facebook event objects into canonical FB events.",
    )
    dedup_fb_parser.add_argument("--db", required=True, type=Path, help="SQLite database path")
    dedup_fb_parser.add_argument("--fb-page-id", default=None, help="Optional FB page id scope.")
    dedup_fb_parser.add_argument("--game-name", default=None, help="Optional canonical game name scope.")
    dedup_fb_parser.add_argument("--page-name", default=None, help="Optional page name scope.")

    llm_merge_fb_parser = subparsers.add_parser(
        "build-fb-events-llm",
        help="Merge and deduplicate raw Facebook event objects into canonical FB events with an LLM.",
    )
    llm_merge_fb_parser.add_argument("--db", required=True, type=Path, help="SQLite database path")
    llm_merge_fb_parser.add_argument("--fb-page-id", default=None, help="Optional FB page id scope.")
    llm_merge_fb_parser.add_argument("--game-name", default=None, help="Optional canonical game name scope.")
    llm_merge_fb_parser.add_argument("--page-name", default=None, help="Optional page name scope.")
    llm_merge_fb_parser.add_argument("--limit", type=int, default=None, help="Optional max raw event objects to merge.")

    unified_merge_parser = subparsers.add_parser(
        "build-unified-events-llm",
        help="Build final unified cross-source events directly from raw FB posts plus ST deterministic events.",
    )
    unified_merge_parser.add_argument("--db", required=True, type=Path, help="SQLite database path")
    unified_merge_parser.add_argument("--unified-app-id", default=None, help="Optional unified_app_id scope.")
    unified_merge_parser.add_argument("--month", default=None, help="Optional YYYY-MM scope.")
    unified_merge_parser.add_argument(
        "--limit-source-rows",
        type=int,
        default=None,
        help="Optional max normalized source rows to merge, for testing only.",
    )

    rerun_step5_parser = subparsers.add_parser(
        "rerun-unified-step5",
        help="Rerun only the final step-5 unified consolidation from saved step-3 and step-4 candidate snapshots.",
    )
    rerun_step5_parser.add_argument("--db", required=True, type=Path, help="SQLite database path")
    rerun_step5_parser.add_argument("--unified-app-id", required=True, help="Scoped unified_app_id.")
    rerun_step5_parser.add_argument("--month", required=True, help="Scoped YYYY-MM month.")
    rerun_step5_parser.add_argument(
        "--source-run-id",
        default=None,
        help="Optional saved full-build run_id to reuse as the step-3/4 snapshot source.",
    )

    serve_api_parser = subparsers.add_parser(
        "serve-api",
        help="Serve the read-only warehouse HTTP API.",
    )
    serve_api_parser.add_argument("--db", required=True, type=Path, help="SQLite database path")
    serve_api_parser.add_argument("--host", default="127.0.0.1", help="Bind host. Defaults to 127.0.0.1.")
    serve_api_parser.add_argument("--port", type=int, default=8000, help="Bind port. Defaults to 8000.")

    ngrok_api_parser = subparsers.add_parser(
        "serve-api-ngrok",
        help="Serve the read-only warehouse HTTP API and expose it through a temporary ngrok tunnel.",
    )
    ngrok_api_parser.add_argument("--db", required=True, type=Path, help="SQLite database path")
    ngrok_api_parser.add_argument("--host", default="127.0.0.1", help="Bind host. Defaults to 127.0.0.1.")
    ngrok_api_parser.add_argument("--port", type=int, default=8765, help="Bind port. Defaults to 8765.")
    ngrok_api_parser.add_argument(
        "--ngrok-authtoken",
        default=None,
        help="Optional ngrok authtoken. Falls back to NGROK_AUTHTOKEN if omitted.",
    )
    ngrok_api_parser.add_argument(
        "--ngrok-domain",
        default=None,
        help="Optional reserved ngrok domain, for example api-name.ngrok.app. Falls back to NGROK_DOMAIN if omitted.",
    )

    preview_fb_parser = subparsers.add_parser(
        "preview-fb-event-dedup",
        help="Preview how many FB event-object pairs will be rule-merged, rule-rejected, or sent to the LLM judge.",
    )
    preview_fb_parser.add_argument("--db", required=True, type=Path, help="SQLite database path")
    preview_fb_parser.add_argument("--fb-page-id", default=None, help="Optional FB page id scope.")
    preview_fb_parser.add_argument("--game-name", default=None, help="Optional canonical game name scope.")
    preview_fb_parser.add_argument("--page-name", default=None, help="Optional page name scope.")

    raw_parser = subparsers.add_parser(
        "sync-sensortower-raw",
        help="Fetch raw SensorTower JSON snapshots for the tracked unified_app_id registry.",
    )
    raw_parser.add_argument("--config", required=True, type=Path, help="JSON config path")
    raw_window = raw_parser.add_mutually_exclusive_group(required=False)
    raw_window.add_argument("--since", type=date.fromisoformat, default=None, help="Start date in YYYY-MM-DD format.")
    raw_window.add_argument("--lookback-days", type=int, default=None, help="Rolling window in days.")
    raw_parser.add_argument("--until", type=date.fromisoformat, default=None, help="Optional end date in YYYY-MM-DD.")
    raw_parser.add_argument(
        "--output-dir",
        default=None,
        help="Directory for raw SensorTower snapshots. Defaults to data_ingest/sensortower/raw.",
    )
    raw_parser.add_argument(
        "--unified-app-id",
        dest="unified_app_ids",
        action="append",
        default=None,
        help="Optional tracked unified_app_id to limit extraction. Repeatable.",
    )

    load_raw_parser = subparsers.add_parser(
        "load-sensortower-raw",
        help="Load pending raw SensorTower manifests and rebuild deterministic ST update events.",
    )
    load_raw_parser.add_argument("--db", required=True, type=Path, help="SQLite database path")
    load_raw_parser.add_argument(
        "--input-dir",
        default=None,
        help="Root directory containing raw SensorTower runs. Defaults to data_ingest/sensortower/raw.",
    )
    load_raw_parser.add_argument(
        "--manifest-path",
        default=None,
        help="Optional specific manifest.json to load instead of scanning the raw root.",
    )
    load_raw_parser.add_argument("--force", action="store_true", help="Reload a manifest even if it was already loaded.")

    summary_parser = subparsers.add_parser("summary", help="Print warehouse table counts")
    summary_parser.add_argument("--db", required=True, type=Path, help="SQLite database path")

    return parser


def _load_sensortower_token() -> str:
    token = os.getenv("SENSOR_TOWER_AUTH_TOKEN", "").strip()
    if not token:
        raise RuntimeError("SENSOR_TOWER_AUTH_TOKEN is missing.")
    return token


def run_sensortower_raw_extract(
    *,
    config_path: Path,
    since: date | None,
    until: date | None,
    lookback_days: int | None,
    output_dir: str | None,
    unified_app_ids: list[str] | None,
) -> None:
    config = load_pipeline_config(config_path)
    targets = resolve_tracked_targets(config, unified_app_ids)
    if not targets:
        raise RuntimeError("No SensorTower targets matched the requested unified_app_ids.")

    window = resolve_raw_window(
        since=since,
        until=until,
        lookback_days=lookback_days,
    )
    client = SensorTowerClient(
        base_url=os.getenv("SENSOR_TOWER_BASE_URL", "https://api.sensortower.com").rstrip("/"),
        auth_token=_load_sensortower_token(),
    )
    summary = extract_sensortower_raw(
        client=client,
        targets=targets,
        window=window,
        output_dir=Path(output_dir) if output_dir else default_sensortower_raw_dir(),
    )
    print(
        "sensortower_raw_extract: "
        f"run_id={summary.run_id} "
        f"snapshot_count={summary.snapshot_count} "
        f"manifest_path={summary.manifest_path}"
    )


def load_sensortower_raw(*, db_path: Path, input_dir: str | None, manifest_path: str | None, force: bool) -> None:
    conn = open_connection(db_path)
    try:
        if manifest_path:
            summaries = [load_sensortower_raw_manifest(conn, manifest_path=Path(manifest_path), force=force)]
        else:
            summaries = load_pending_sensortower_raw_manifests(
                conn,
                raw_root=Path(input_dir) if input_dir else default_sensortower_raw_dir(),
                force=force,
            )
    finally:
        conn.close()

    if not summaries:
        print("No pending SensorTower raw manifests found.")
        return

    for summary in summaries:
        print(
            "sensortower_raw_load: "
            f"manifest_path={summary.manifest_path} "
            f"loaded_snapshots={summary.loaded_snapshots} "
            f"skipped_snapshots={summary.skipped_snapshots} "
            f"update_rows={summary.update_rows} "
            f"version_rows={summary.version_rows} "
            f"st_update_event_rows={summary.st_update_event_rows} "
            f"st_version_event_rows={summary.st_version_event_rows}"
        )


def main() -> int:
    load_environment_files()
    parser = build_parser()
    args = parser.parse_args()

    if args.command == "init-db":
        conn = open_connection(args.db)
        try:
            init_db(conn)
        finally:
            conn.close()
        print(f"Initialized warehouse schema at {args.db}")
        return 0

    if args.command == "run":
        stats = run_etl(args.db, args.config, args.input_dir)
        print(
            "ETL completed: "
            f"raw_fb_posts={stats.raw_fb_posts}, "
            f"raw_app_updates={stats.raw_app_updates}, "
            f"raw_versions={stats.raw_versions}, "
            f"st_app_update_events_loaded={stats.st_app_update_events_loaded}, "
            f"st_version_events_loaded={stats.st_version_events_loaded}"
        )
        return 0

    if args.command == "reload-fb-posts":
        stats = reload_fb_posts(args.db, args.config, args.input_dir)
        print(
            "FB reload completed: "
            f"raw_fb_posts={stats.raw_fb_posts}, "
            f"st_app_update_events_loaded={stats.st_app_update_events_loaded}, "
            f"st_version_events_loaded={stats.st_version_events_loaded}"
        )
        return 0

    if args.command == "build-fb-event-detection":
        conn = open_connection(args.db)
        try:
            init_db(conn)
            stats = build_fb_event_detection(
                conn,
                fb_page_id=args.fb_page_id,
                game_name=args.game_name,
                page_name=args.page_name,
                limit=args.limit,
                progress=print,
            )
        finally:
            conn.close()
        print(
            "fb_event_detection_completed: "
            f"processed_posts={stats.processed_posts} "
            f"detected_posts={stats.detected_posts}"
        )
        return 0

    if args.command == "build-fb-event-objects":
        conn = open_connection(args.db)
        try:
            init_db(conn)
            stats = build_fb_event_objects(
                conn,
                fb_page_id=args.fb_page_id,
                game_name=args.game_name,
                page_name=args.page_name,
                limit=args.limit,
                progress=print,
            )
        finally:
            conn.close()
        print(
            "fb_event_objects_completed: "
            f"processed_posts={stats.processed_posts} "
            f"extracted_objects={stats.extracted_objects}"
        )
        return 0

    if args.command == "build-fb-raw-events":
        conn = open_connection(args.db)
        try:
            init_db(conn)
            stats = build_fb_raw_events(
                conn,
                fb_page_id=args.fb_page_id,
                game_name=args.game_name,
                page_name=args.page_name,
                limit=args.limit,
                progress=print,
            )
        finally:
            conn.close()
        print(
            "fb_raw_events_completed: "
            f"detection_processed_posts={stats.detection_processed_posts} "
            f"detected_posts={stats.detected_posts} "
            f"extraction_processed_posts={stats.extraction_processed_posts} "
            f"extracted_objects={stats.extracted_objects}"
        )
        return 0

    if args.command == "build-fb-events":
        conn = open_connection(args.db)
        try:
            init_db(conn)
            stats = build_fb_events(
                conn,
                fb_page_id=args.fb_page_id,
                game_name=args.game_name,
                page_name=args.page_name,
            )
        finally:
            conn.close()
        print(
            "fb_events_completed: "
            f"candidate_pairs={stats.candidate_pairs} "
            f"judged_pairs={stats.judged_pairs} "
            f"fb_events={stats.fb_events}"
        )
        return 0

    if args.command == "build-fb-events-llm":
        conn = open_connection(args.db)
        try:
            init_db(conn)
            stats = build_fb_events_with_llm_merge(
                conn,
                fb_page_id=args.fb_page_id,
                game_name=args.game_name,
                page_name=args.page_name,
                limit=args.limit,
            )
        finally:
            conn.close()
        print(
            "fb_events_llm_completed: "
            f"merge_groups={stats.merge_groups} "
            f"source_objects={stats.source_objects} "
            f"merged_events={stats.merged_events}"
        )
        return 0

    if args.command == "build-unified-events-llm":
        conn = open_connection(args.db)
        try:
            init_db(conn)
            stats = build_unified_events_with_llm_merge(
                conn,
                unified_app_id=args.unified_app_id,
                month=args.month,
                limit_source_rows=args.limit_source_rows,
                progress=print,
            )
        finally:
            conn.close()
        print(
            "unified_events_llm_completed: "
            f"merge_scopes={stats.merge_scopes} "
            f"source_rows={stats.source_rows} "
            f"merged_events={stats.merged_events}"
        )
        return 0

    if args.command == "rerun-unified-step5":
        conn = open_connection(args.db)
        try:
            init_db(conn)
            stats = rerun_unified_step5(
                conn,
                unified_app_id=args.unified_app_id,
                month=args.month,
                source_run_id=args.source_run_id,
                progress=print,
            )
        finally:
            conn.close()
        print(
            "unified_step5_rerun_completed: "
            f"merge_scopes={stats.merge_scopes} "
            f"source_rows={stats.source_rows} "
            f"merged_events={stats.merged_events}"
        )
        return 0

    if args.command == "serve-api":
        conn = open_connection(args.db)
        try:
            init_db(conn)
        finally:
            conn.close()
        uvicorn.run(create_app(db_path=args.db), host=args.host, port=args.port)
        return 0

    if args.command == "serve-api-ngrok":
        conn = open_connection(args.db)
        try:
            init_db(conn)
        finally:
            conn.close()
        serve_api_with_ngrok(
            db_path=args.db,
            host=args.host,
            port=args.port,
            ngrok_authtoken=args.ngrok_authtoken,
            ngrok_domain=args.ngrok_domain,
            progress=print,
        )
        return 0

    if args.command == "preview-fb-event-dedup":
        conn = open_connection(args.db)
        try:
            init_db(conn)
            stats = preview_fb_event_dedup(
                conn,
                fb_page_id=args.fb_page_id,
                game_name=args.game_name,
                page_name=args.page_name,
            )
        finally:
            conn.close()
        print(
            "fb_event_dedup_preview: "
            f"candidate_pairs={stats.candidate_pairs} "
            f"rule_merge_pairs={stats.rule_merge_pairs} "
            f"rule_reject_pairs={stats.rule_reject_pairs} "
            f"llm_judge_pairs={stats.llm_judge_pairs}"
        )
        return 0

    if args.command == "sync-sensortower-raw":
        run_sensortower_raw_extract(
            config_path=args.config,
            since=args.since,
            until=args.until,
            lookback_days=args.lookback_days,
            output_dir=args.output_dir,
            unified_app_ids=args.unified_app_ids,
        )
        return 0

    if args.command == "load-sensortower-raw":
        load_sensortower_raw(
            db_path=args.db,
            input_dir=args.input_dir,
            manifest_path=args.manifest_path,
            force=args.force,
        )
        return 0

    if args.command == "summary":
        summary = summarize_db(args.db)
        for table_name, count in summary.items():
            print(f"{table_name}: {count}")
        return 0

    parser.error("Unknown command")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
