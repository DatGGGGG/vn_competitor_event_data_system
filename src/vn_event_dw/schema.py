"""SQLite schema for the mini data warehouse."""

SCHEMA_SQL = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS etl_runs (
    run_id TEXT PRIMARY KEY,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    status TEXT NOT NULL,
    details_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS config_app_mapping (
    unified_app_id TEXT NOT NULL,
    fb_page_id TEXT NOT NULL,
    app_name TEXT NOT NULL,
    is_active INTEGER NOT NULL DEFAULT 1,
    valid_from TEXT,
    valid_to TEXT,
    source_updated_at TEXT,
    PRIMARY KEY (unified_app_id, fb_page_id)
);

CREATE TABLE IF NOT EXISTS raw_fb_posts (
    source_post_id TEXT PRIMARY KEY,
    unified_app_id TEXT NOT NULL DEFAULT '',
    fb_page_id TEXT NOT NULL,
    channel_id TEXT NOT NULL,
    channel_name TEXT NOT NULL,
    post_type TEXT NOT NULL,
    post_description TEXT NOT NULL,
    duration TEXT NOT NULL,
    link TEXT NOT NULL,
    publish_time TEXT NOT NULL,
    hashtag TEXT NOT NULL,
    engagement TEXT NOT NULL,
    reaction TEXT NOT NULL,
    comment TEXT NOT NULL,
    share TEXT NOT NULL,
    view TEXT NOT NULL,
    source_file TEXT NOT NULL,
    ingested_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS raw_st_app_update (
    source_update_id TEXT PRIMARY KEY,
    unified_app_id TEXT NOT NULL,
    os TEXT NOT NULL DEFAULT '',
    app_id TEXT NOT NULL DEFAULT '',
    country TEXT NOT NULL DEFAULT '',
    update_time TEXT NOT NULL,
    update_type TEXT NOT NULL,
    name TEXT,
    subtitle TEXT,
    short_description TEXT,
    description_text TEXT,
    description_before_text TEXT,
    description_after_text TEXT,
    description_diff_html TEXT,
    version_before TEXT,
    version_after TEXT,
    version_summary TEXT,
    events_json TEXT,
    channel_raw TEXT,
    notes_raw TEXT,
    advisory_raw TEXT,
    apple_watch_enabled_raw TEXT,
    apple_watch_icon_raw TEXT,
    apple_watch_screenshot_raw TEXT,
    category_raw TEXT,
    contains_ad_raw TEXT,
    content_rating_raw TEXT,
    country_raw TEXT,
    custom_product_pages_raw TEXT,
    description_raw TEXT,
    events_raw TEXT,
    feature_graphic_raw TEXT,
    featured_user_feedback_raw TEXT,
    file_size_raw TEXT,
    icon_raw TEXT,
    imessage_enabled_raw TEXT,
    imessage_icon_raw TEXT,
    imessage_screenshot_raw TEXT,
    install_range_raw TEXT,
    minimum_os_version_raw TEXT,
    name_raw TEXT,
    price_raw TEXT,
    promo_text_raw TEXT,
    publisher_id_raw TEXT,
    publisher_name_raw TEXT,
    related_app_raw TEXT,
    screenshot_raw TEXT,
    sdk_id_raw TEXT,
    short_description_raw TEXT,
    subtitle_raw TEXT,
    support_url_raw TEXT,
    supported_device_raw TEXT,
    supported_language_raw TEXT,
    top_in_app_purchase_raw TEXT,
    payload_unified_app_id_raw TEXT,
    version_raw TEXT,
    raw_payload TEXT,
    update_payload TEXT,
    source_file TEXT NOT NULL,
    ingested_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS raw_st_version (
    source_version_id TEXT PRIMARY KEY,
    unified_app_id TEXT NOT NULL,
    os TEXT NOT NULL DEFAULT '',
    app_id TEXT NOT NULL DEFAULT '',
    country TEXT NOT NULL DEFAULT '',
    version_time TEXT NOT NULL,
    version_name TEXT NOT NULL,
    before_version TEXT,
    after_version TEXT,
    version_summary TEXT,
    raw_payload TEXT,
    version_payload TEXT,
    source_file TEXT NOT NULL,
    ingested_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS st_app_update_events (
    st_update_event_id TEXT PRIMARY KEY,
    event_id TEXT NOT NULL,
    source_row_id TEXT NOT NULL,
    unified_app_id TEXT NOT NULL,
    event_name TEXT NOT NULL,
    estimated_start_date TEXT,
    estimated_end_date TEXT,
    event_description TEXT,
    source_refs TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_st_app_update_events_app_time
    ON st_app_update_events (unified_app_id, estimated_start_date);

CREATE INDEX IF NOT EXISTS idx_st_app_update_events_event_id
    ON st_app_update_events (event_id);

CREATE TABLE IF NOT EXISTS st_version_events (
    st_version_event_id TEXT PRIMARY KEY,
    source_row_id TEXT NOT NULL,
    unified_app_id TEXT NOT NULL,
    event_name TEXT NOT NULL,
    estimated_start_date TEXT,
    estimated_end_date TEXT,
    event_description TEXT,
    source_refs TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_st_version_events_app_time
    ON st_version_events (unified_app_id, estimated_start_date);

CREATE TABLE IF NOT EXISTS post_event_detection (
    post_id TEXT PRIMARY KEY,
    unified_app_id TEXT NOT NULL DEFAULT '',
    fb_page_id TEXT NOT NULL,
    channel_id TEXT NOT NULL,
    page_name TEXT NOT NULL,
    game_name TEXT NOT NULL,
    post_time TEXT NOT NULL,
    contains_event INTEGER NOT NULL,
    detection_confidence REAL NOT NULL,
    detection_reason TEXT NOT NULL,
    event_signals TEXT NOT NULL,
    post_text_hash TEXT NOT NULL,
    llm_model TEXT NOT NULL,
    prompt_version TEXT NOT NULL,
    processed_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_post_event_detection_game_time
    ON post_event_detection (game_name, post_time);

CREATE INDEX IF NOT EXISTS idx_post_event_detection_unified_app_time
    ON post_event_detection (unified_app_id, post_time);

CREATE TABLE IF NOT EXISTS post_event_objects (
    event_object_id TEXT PRIMARY KEY,
    post_id TEXT NOT NULL,
    unified_app_id TEXT NOT NULL DEFAULT '',
    fb_page_id TEXT NOT NULL,
    page_name TEXT NOT NULL,
    game_name TEXT NOT NULL,
    post_time TEXT NOT NULL,
    event_name TEXT NOT NULL,
    estimated_start_date TEXT,
    estimated_end_date TEXT,
    event_description TEXT NOT NULL,
    evidence_text TEXT NOT NULL,
    extraction_confidence REAL NOT NULL,
    llm_model TEXT NOT NULL,
    prompt_version TEXT NOT NULL,
    processed_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_post_event_objects_post_id
    ON post_event_objects (post_id);

CREATE INDEX IF NOT EXISTS idx_post_event_objects_game_time
    ON post_event_objects (game_name, post_time);

CREATE INDEX IF NOT EXISTS idx_post_event_objects_unified_app_time
    ON post_event_objects (unified_app_id, post_time);

CREATE TABLE IF NOT EXISTS fb_event_match_decisions (
    pair_id TEXT PRIMARY KEY,
    left_event_object_id TEXT NOT NULL,
    right_event_object_id TEXT NOT NULL,
    name_similarity REAL NOT NULL,
    description_similarity REAL NOT NULL,
    date_similarity REAL NOT NULL,
    page_game_similarity REAL NOT NULL,
    dedup_score REAL NOT NULL,
    decision_source TEXT NOT NULL,
    same_event INTEGER NOT NULL,
    judge_confidence REAL NOT NULL,
    judge_reason TEXT NOT NULL,
    llm_model TEXT NOT NULL,
    prompt_version TEXT NOT NULL,
    processed_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_fb_event_match_decisions_left_right
    ON fb_event_match_decisions (left_event_object_id, right_event_object_id);

CREATE TABLE IF NOT EXISTS fb_events (
    event_id TEXT PRIMARY KEY,
    unified_app_id TEXT NOT NULL DEFAULT '',
    canonical_event_name TEXT NOT NULL,
    estimated_start_date TEXT,
    estimated_end_date TEXT,
    canonical_event_description TEXT NOT NULL,
    game_name TEXT NOT NULL,
    page_name TEXT NOT NULL,
    source_post_ids TEXT NOT NULL,
    source_event_object_ids TEXT NOT NULL,
    first_seen_post_time TEXT NOT NULL,
    last_seen_post_time TEXT NOT NULL,
    num_source_posts INTEGER NOT NULL,
    total_engagement INTEGER NOT NULL,
    dedup_confidence REAL NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_fb_events_game_time
    ON fb_events (game_name, estimated_start_date);

CREATE INDEX IF NOT EXISTS idx_fb_events_unified_app_time
    ON fb_events (unified_app_id, estimated_start_date);

CREATE TABLE IF NOT EXISTS unified_events (
    unified_event_id TEXT PRIMARY KEY,
    unified_app_id TEXT NOT NULL,
    month_bucket TEXT NOT NULL,
    canonical_event_name TEXT NOT NULL,
    event_category TEXT NOT NULL,
    estimated_start_date TEXT,
    estimated_end_date TEXT,
    canonical_event_description TEXT NOT NULL,
    anchor_source_type TEXT NOT NULL,
    merge_confidence REAL NOT NULL,
    merge_model TEXT NOT NULL,
    prompt_version TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_unified_events_app_month
    ON unified_events (unified_app_id, month_bucket);

CREATE TABLE IF NOT EXISTS unified_event_sources (
    unified_event_id TEXT NOT NULL,
    source_type TEXT NOT NULL,
    source_id TEXT NOT NULL,
    source_time TEXT,
    source_post_id TEXT,
    source_confidence REAL,
    PRIMARY KEY (unified_event_id, source_type, source_id)
);

CREATE INDEX IF NOT EXISTS idx_unified_event_sources_source
    ON unified_event_sources (source_type, source_id);

CREATE TABLE IF NOT EXISTS unified_event_merge_runs (
    run_id TEXT PRIMARY KEY,
    session_id TEXT,
    unified_app_id TEXT NOT NULL,
    month_bucket TEXT NOT NULL,
    source_row_count INTEGER NOT NULL,
    merged_event_count INTEGER NOT NULL,
    model TEXT NOT NULL,
    prompt_version TEXT NOT NULL,
    build_mode TEXT NOT NULL DEFAULT 'full',
    source_snapshot_run_id TEXT,
    llm_input_tokens INTEGER NOT NULL DEFAULT 0,
    llm_cached_input_tokens INTEGER NOT NULL DEFAULT 0,
    llm_output_tokens INTEGER NOT NULL DEFAULT 0,
    llm_total_tokens INTEGER NOT NULL DEFAULT 0,
    llm_total_cost_usd REAL NOT NULL DEFAULT 0,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    status TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_unified_event_merge_runs_app_month
    ON unified_event_merge_runs (unified_app_id, month_bucket);

CREATE TABLE IF NOT EXISTS llm_usage_log (
    usage_id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT,
    run_id TEXT,
    unified_app_id TEXT,
    month_bucket TEXT,
    stage TEXT NOT NULL,
    item_id TEXT,
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    prompt_version TEXT NOT NULL,
    response_id TEXT,
    input_tokens INTEGER NOT NULL,
    cached_input_tokens INTEGER NOT NULL DEFAULT 0,
    uncached_input_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL,
    total_tokens INTEGER NOT NULL,
    input_cost_usd REAL NOT NULL DEFAULT 0,
    cached_input_cost_usd REAL NOT NULL DEFAULT 0,
    output_cost_usd REAL NOT NULL DEFAULT 0,
    total_cost_usd REAL NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_llm_usage_log_session
    ON llm_usage_log (session_id, created_at);

CREATE INDEX IF NOT EXISTS idx_llm_usage_log_run
    ON llm_usage_log (run_id, created_at);

CREATE INDEX IF NOT EXISTS idx_llm_usage_log_scope
    ON llm_usage_log (unified_app_id, month_bucket, created_at);

CREATE TABLE IF NOT EXISTS unified_event_step3_candidates (
    run_id TEXT NOT NULL,
    unified_app_id TEXT NOT NULL,
    month_bucket TEXT NOT NULL,
    candidate_id TEXT NOT NULL,
    canonical_event_name TEXT NOT NULL,
    event_category TEXT NOT NULL,
    estimated_start_date TEXT,
    estimated_end_date TEXT,
    canonical_event_description TEXT NOT NULL,
    anchor_source_type TEXT NOT NULL,
    merge_confidence REAL NOT NULL,
    merge_model TEXT NOT NULL,
    prompt_version TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (run_id, candidate_id)
);

CREATE INDEX IF NOT EXISTS idx_unified_event_step3_candidates_app_month
    ON unified_event_step3_candidates (unified_app_id, month_bucket);

CREATE TABLE IF NOT EXISTS unified_event_step3_candidate_sources (
    run_id TEXT NOT NULL,
    candidate_id TEXT NOT NULL,
    source_type TEXT NOT NULL,
    source_id TEXT NOT NULL,
    source_time TEXT,
    source_post_id TEXT,
    source_confidence REAL,
    PRIMARY KEY (run_id, candidate_id, source_type, source_id)
);

CREATE INDEX IF NOT EXISTS idx_unified_event_step3_candidate_sources_source
    ON unified_event_step3_candidate_sources (source_type, source_id);

CREATE TABLE IF NOT EXISTS unified_event_step4_harvest_candidates (
    run_id TEXT NOT NULL,
    unified_app_id TEXT NOT NULL,
    month_bucket TEXT NOT NULL,
    candidate_id TEXT NOT NULL,
    canonical_event_name TEXT NOT NULL,
    event_category TEXT NOT NULL,
    estimated_start_date TEXT,
    estimated_end_date TEXT,
    canonical_event_description TEXT NOT NULL,
    anchor_source_type TEXT NOT NULL,
    merge_confidence REAL NOT NULL,
    merge_model TEXT NOT NULL,
    prompt_version TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (run_id, candidate_id)
);

CREATE INDEX IF NOT EXISTS idx_unified_event_step4_harvest_candidates_app_month
    ON unified_event_step4_harvest_candidates (unified_app_id, month_bucket);

CREATE TABLE IF NOT EXISTS unified_event_step4_harvest_candidate_sources (
    run_id TEXT NOT NULL,
    candidate_id TEXT NOT NULL,
    source_type TEXT NOT NULL,
    source_id TEXT NOT NULL,
    source_time TEXT,
    source_post_id TEXT,
    source_confidence REAL,
    PRIMARY KEY (run_id, candidate_id, source_type, source_id)
);

CREATE INDEX IF NOT EXISTS idx_unified_event_step4_harvest_candidate_sources_source
    ON unified_event_step4_harvest_candidate_sources (source_type, source_id);

CREATE TABLE IF NOT EXISTS unified_event_step5_final_candidates (
    run_id TEXT NOT NULL,
    unified_app_id TEXT NOT NULL,
    month_bucket TEXT NOT NULL,
    candidate_id TEXT NOT NULL,
    canonical_event_name TEXT NOT NULL,
    event_category TEXT NOT NULL,
    estimated_start_date TEXT,
    estimated_end_date TEXT,
    canonical_event_description TEXT NOT NULL,
    anchor_source_type TEXT NOT NULL,
    merge_confidence REAL NOT NULL,
    merge_model TEXT NOT NULL,
    prompt_version TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (run_id, candidate_id)
);

CREATE INDEX IF NOT EXISTS idx_unified_event_step5_final_candidates_app_month
    ON unified_event_step5_final_candidates (unified_app_id, month_bucket);

CREATE TABLE IF NOT EXISTS unified_event_step5_final_candidate_sources (
    run_id TEXT NOT NULL,
    candidate_id TEXT NOT NULL,
    source_type TEXT NOT NULL,
    source_id TEXT NOT NULL,
    source_time TEXT,
    source_post_id TEXT,
    source_confidence REAL,
    PRIMARY KEY (run_id, candidate_id, source_type, source_id)
);

CREATE INDEX IF NOT EXISTS idx_unified_event_step5_final_candidate_sources_source
    ON unified_event_step5_final_candidate_sources (source_type, source_id);
"""
