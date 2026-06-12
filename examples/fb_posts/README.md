# FB Post Drop Folder

Put one or more FB post CSV files in this folder.

The ETL will read every `*.csv` file here recursively when you run:

```bash
python -m vn_event_dw.cli run --db data/warehouse.db --config examples/config.json --input-dir examples
```

Required columns in each CSV:

- `source_post_id`
- `fb_page_id`
- `post_time`
- `post_content`

File names can encode the game and period however you want.

The loader also accepts the richer export headers you showed:

- `Post id`
- `Channel id`
- `Publish time`
- `Post description`
- `Link` as a fallback when `Post description` is blank

Those are stored into the raw table as:

- `source_post_id`
- `fb_page_id`
- `channel_id`
- `channel_name`
- `post_type`
- `post_description`
- `duration`
- `link`
- `publish_time`
- `hashtag`
- `engagement`
- `reaction`
- `comment`
- `share`
- `view`
