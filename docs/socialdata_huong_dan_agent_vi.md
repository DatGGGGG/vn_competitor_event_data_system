# Hướng dẫn cho Claude/Codex truy cập Socialdata

Tài liệu này dành cho:

- người cần giao việc cho Claude/Codex
- agent cần reuse connector Socialdata có sẵn trong repo

Mục tiêu là giúp agent vào đúng hệ thống Socialdata mà không phải tự viết lại auth flow.

## 1. Nguyên tắc bắt buộc

Agent phải:

1. reuse code có sẵn trong repo
2. dùng đúng Google OAuth scope
3. test auth trước khi đi tìm GraphQL query hay sync bài viết

Không được:

- tự phát minh auth flow mới
- dùng `cloud-platform` làm scope duy nhất
- yêu cầu người dùng paste private key vào chat

## 2. File code cần reuse

Đây là những file là nguồn sự thật trong repo:

- `src/vn_event_dw/socialdata.py`
- `src/vn_event_dw/socialdata_sync.py`
- `src/vn_event_dw/cli.py`
- `scripts/socialdata_mint_google_access_token.mjs`

## 3. Input tối thiểu agent cần có

Agent cần được cung cấp:

- đường dẫn repo
- đường dẫn file service-account JSON
- Socialdata app slug:
  `srcvn`
- mục tiêu cụ thể:
  - auth check
  - introspect schema
  - tìm query post
  - sync bài viết

## 4. Scope đúng

Scope mặc định phải là:

```text
https://www.googleapis.com/auth/userinfo.email
```

Lý do:

- Socialdata cần nhìn thấy email của service account trong Google token
- nếu mint bằng scope khác, team đã gặp lỗi `Invalid Email`

## 5. Thứ tự chạy để test

### Bước 1: auth check

```powershell
python -m vn_event_dw.cli socialdata-auth-check `
  --google-service-account-file C:\Users\VEE0634\Desktop\Coding\vn_competitor_event_data_system\local_secrets\socialdata-reader-2.json
```

### Bước 2: introspect schema nếu cần

```powershell
python -m vn_event_dw.cli socialdata-introspect `
  --google-service-account-file C:\Users\VEE0634\Desktop\Coding\vn_competitor_event_data_system\local_secrets\socialdata-reader-2.json `
  --output tmp/socialdata_schema.json
```

### Bước 3: query GraphQL thủ công nếu cần debug

```powershell
python -m vn_event_dw.cli socialdata-graphql `
  --google-service-account-file C:\Users\VEE0634\Desktop\Coding\vn_competitor_event_data_system\local_secrets\socialdata-reader-2.json `
  --query "query { __typename }"
```

### Bước 4: sync bài viết

```powershell
python -m vn_event_dw.cli sync-socialdata-posts `
  --db data/warehouse.db `
  --config examples/config.json `
  --lookback-days 10 `
  --google-service-account-file C:\Users\VEE0634\Desktop\Coding\vn_competitor_event_data_system\local_secrets\socialdata-reader-2.json
```

## 6. Hành vi mong đợi của agent

Khi được giao việc, agent nên:

1. đọc skill tại `skills/socialdata-connector`
2. reuse `SocialDataClient` và CLI có sẵn
3. auth check trước
4. chỉ sync sau khi auth ổn
5. nếu lỗi, báo cáo rõ:
   - command đã chạy
   - output lỗi
   - nguyên nhân nghi ngờ
   - đề xuất fix tiếp theo

## 7. Prompt sẵn để paste cho Claude/Codex

```text
Bạn đang làm việc trong repo vn_competitor_event_data_system.

Yêu cầu:
- Reuse connector Socialdata có sẵn, không được viết lại auth flow từ đầu.
- Đọc và áp dụng skill tại ./skills/socialdata-connector.
- File Google service-account JSON đã có sẵn trong local_secrets.
- Socialdata app slug là srcvn.

Thứ tự thực hiện:
1. Xác nhận auth bằng socialdata-auth-check
2. Nếu cần, introspect schema
3. Nếu cần, query GraphQL để tìm post/channel queries
4. Nếu mục tiêu là nạp dữ liệu, chạy sync-socialdata-posts

Bắt buộc:
- Scope mặc định phải là https://www.googleapis.com/auth/userinfo.email
- Báo cáo command, kết quả và next step rõ ràng
- Không xin người dùng paste private key vào chat nếu file đã có trên máy
```

## 8. Nếu muốn giao việc cho agent ở repo khác

Nếu teammate không chạy trực tiếp repo này, có 2 cách:

1. Cách tốt nhất:
   cho agent làm việc ngay trong repo `vn_competitor_event_data_system`

2. Cách thay thế:
   copy skill `skills/socialdata-connector`
   và nói cho agent reuse logic trong:
   - `src/vn_event_dw/socialdata.py`
   - `src/vn_event_dw/socialdata_sync.py`
   - `src/vn_event_dw/cli.py`

## 9. Định nghĩa thành công

Thành công mức cơ bản:

- `socialdata-auth-check` trả về `__typename`

Thành công mức trung gian:

- introspect schema ra file JSON
- GraphQL query chạy được

Thành công mức đầy đủ:

- `sync-socialdata-posts` lấy được post và upsert vào `raw_fb_posts`
