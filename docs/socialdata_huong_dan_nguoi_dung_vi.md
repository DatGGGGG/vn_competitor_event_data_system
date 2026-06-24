# Hướng dẫn Socialdata cho người không kỹ thuật

Tài liệu này dành cho người không biết code nhưng vẫn cần tự setup quyền truy cập Socialdata và phối hợp với Claude/Codex.

Mục tiêu:

- tạo Google service account
- tải JSON key đúng cách
- cấp quyền email đó trong Socialdata
- test xem connector đã truy cập được chưa
- đưa đúng thông tin cho Claude/Codex để agent làm phần kỹ thuật

## 1. Bạn cần chuẩn bị gì

Trước khi bắt đầu, bạn cần có:

- tài khoản Google có quyền vào được 1 Google Cloud project
- quyền vào trang Socialdata của team
- repo này trên máy:
  `C:\Users\VEE0634\Desktop\Coding\vn_competitor_event_data_system`
- Python đã cài sẵn trên máy

Nếu bạn chỉ nhờ Claude/Codex làm phần code, bạn vẫn cần tự làm 2 việc tay:

1. tạo service account + JSON key
2. add email service account vào Socialdata

## 1.1 Cách lấy đúng `slug` của team bạn

Không được mặc định slug là `srcvn`.

Mỗi team có thể có slug khác nhau.

Bạn lấy slug ngay trên URL của Socialdata.

Ví dụ:

```text
https://socialdata.garena.vn/srcvn/member/channel
```

thì slug là:

```text
srcvn
```

Nói chung, slug là phần nằm ngay sau `socialdata.garena.vn/` và trước dấu `/` tiếp theo.

## 2. Tạo Google service account

### Cách làm trên web

1. Mở:
   [https://console.cloud.google.com/apis/credentials](https://console.cloud.google.com/apis/credentials)
2. Ở thanh trên cùng, chọn đúng project.
   Nếu team đang dùng project cũ, dùng project đó.
3. Bấm `Create credentials`.
4. Chọn `Service account`.
5. Điền:
   - `Service account name`: ví dụ `socialdata-reader`
   - `Service account ID`: để Google tự sinh hoặc sửa nhẹ nếu cần
6. Bấm `Create and continue`.
7. Nếu không có yêu cầu IAM đặc biệt, có thể bỏ qua bước gán role trong GCP.
8. Bấm `Done`.

### Tạo file JSON key

1. Vào service account vừa tạo.
2. Mở tab `Keys`.
3. Bấm `Add key` -> `Create new key`.
4. Chọn `JSON`.
5. Bấm `Create`.
6. Trình duyệt sẽ tải về 1 file `.json`.

## 3. Lưu file JSON key đúng chỗ

Không gửi file JSON vào chat.
Không commit file JSON lên git.

Hãy copy file vào thư mục local-only trong repo:

```text
C:\Users\VEE0634\Desktop\Coding\vn_competitor_event_data_system\local_secrets\
```

Ví dụ:

```text
C:\Users\VEE0634\Desktop\Coding\vn_competitor_event_data_system\local_secrets\socialdata-reader-2.json
```

Để xem email service account trong file JSON, mở PowerShell tại root repo và chạy:

```powershell
Get-Content .\local_secrets\socialdata-reader-2.json |
  ConvertFrom-Json |
  Select-Object client_email
```

Bạn sẽ thấy email dạng:

```text
socialdata-reader-2@strategy-competitor-dashboard.iam.gserviceaccount.com
```

## 4. Cấp quyền email đó trong Socialdata

1. Mở trang Socialdata của team.
2. Vào màn hình `Users`.
3. Bấm `Add`.
4. Điền:
   - `ID`: có thể để trống nếu hệ thống tự xử lý
   - `Name`: đặt tên để dễ nhận biết, ví dụ `socialdata-reader-2`
   - `Email`: chính là `client_email` trong file JSON
   - `Role`: nếu không có hướng dẫn khác từ dev, dùng role team đang dùng hiện tại
5. Lưu lại.

Lưu ý:

- Nếu email service account chưa được add vào Socialdata, bước exchange token sẽ lỗi `Invalid Email`.
- Đây là lỗi team mình đã gặp trước đó, nên nhớ check kỹ email này.

## 5. Test kết nối bằng lệnh copy-paste

Tất cả lệnh bên dưới chạy trong PowerShell, tại root repo:

```text
C:\Users\VEE0634\Desktop\Coding\vn_competitor_event_data_system
```

### 5.1 Tạo Google access token

```powershell
python -m vn_event_dw.cli socialdata-mint-google-access-token `
  --google-service-account-file C:\Users\VEE0634\Desktop\Coding\vn_competitor_event_data_system\local_secrets\socialdata-reader-2.json
```

Kết quả mong đợi:

- có `service_account_email`
- có `access_token`
- có `expiry_iso`
- scope mặc định phải là:
  `https://www.googleapis.com/auth/userinfo.email`

Nếu chỉ muốn copy token gọn để gửi cho dev test:

```powershell
python -m vn_event_dw.cli socialdata-mint-google-access-token `
  --google-service-account-file C:\Users\VEE0634\Desktop\Coding\vn_competitor_event_data_system\local_secrets\socialdata-reader-2.json `
  --token-only
```

Lưu ý:

- Token này thường chỉ sống khoảng 1 giờ.
- Mỗi lần chạy, token có thể khác nhau. Đây là bình thường.

### 5.2 Exchange Google token sang Socialdata `usession`

Nếu bạn muốn test thủ công theo đúng flow của dev Socialdata:

```powershell
$token = python -m vn_event_dw.cli socialdata-mint-google-access-token `
  --google-service-account-file C:\Users\VEE0634\Desktop\Coding\vn_competitor_event_data_system\local_secrets\socialdata-reader-2.json `
  --token-only
$token = $token.Trim()
curl.exe -i --max-redirs 0 "https://socialdata.garena.vn/connect/google/callback?access_token=$token"
```

Kết quả mong đợi:

- HTTP `302 Found`
- header `set-cookie` có `usession=...`

Nếu thấy `Invalid Email`:

- email service account chưa được add đúng trong Socialdata
- hoặc agent/dev đang mint sai scope

## 6. Test GraphQL nhanh nhất

Dùng lệnh này:

```powershell
python -m vn_event_dw.cli socialdata-auth-check `
  --google-service-account-file C:\Users\VEE0634\Desktop\Coding\vn_competitor_event_data_system\local_secrets\socialdata-reader-2.json
```

Kết quả mong đợi:

```json
{
  "data": {
    "__typename": "Query"
  }
}
```

Nếu ra được như trên, nghĩa là connector đã vào được Socialdata.

## 7. Bạn cần đưa gì cho Claude/Codex

Khi nhờ agent làm việc, bạn chỉ cần đưa:

- đường dẫn repo
- đường dẫn file JSON key
- slug hiện tại của team đó, lấy từ URL Socialdata của họ
- mục tiêu cần làm:
  - test auth
  - introspect schema
  - tìm query post
  - sync bài viết

Không cần paste nội dung file JSON vào chat nếu Claude/Codex đang chạy cùng máy và đọc được file local.

## 8. Prompt sẵn để paste cho Claude/Codex

Bạn có thể paste prompt này:

```text
Bạn đang làm việc trong repo vn_competitor_event_data_system.

Nhiệm vụ:
1. Reuse connector Socialdata có sẵn trong repo, không được viết lại auth flow từ đầu.
2. Đọc và dùng skill tại ./skills/socialdata-connector.
3. File Google service account nằm tại:
   C:\Users\VEE0634\Desktop\Coding\vn_competitor_event_data_system\local_secrets\socialdata-reader-2.json
4. Tự đọc team slug từ URL Socialdata của user, không được hard-code srcvn
5. Hãy chạy đúng thứ tự:
   - socialdata-auth-check
   - socialdata-introspect nếu cần
   - socialdata-graphql để test query
   - sync-socialdata-posts nếu mục tiêu là lấy bài viết

Bắt buộc:
- Scope Google mặc định phải là https://www.googleapis.com/auth/userinfo.email
- Không dùng cloud-platform làm scope duy nhất
- Nếu gặp lỗi, báo cáo rõ command, output và next step
```

## 9. Nếu Claude/Codex hỏi lại, bạn trả lời thế nào

Nếu agent hỏi `file key nằm đâu?`

- trả lời bằng đường dẫn file

Nếu agent hỏi `có thể paste usession không?`

- chỉ paste nếu bạn đang test tay và đã có cookie
- còn không thì bảo agent tự dùng service-account flow

Nếu agent hỏi `nên dùng code nào trong repo?`

- bảo agent reuse:
  - `src/vn_event_dw/socialdata.py`
  - `src/vn_event_dw/socialdata_sync.py`
  - `src/vn_event_dw/cli.py`
  - `scripts/socialdata_mint_google_access_token.mjs`

## 10. Lỗi thường gặp

### `Invalid Email`

Thường do 1 trong 2 lý do:

- email service account chưa được add vào Socialdata
- token được mint với scope sai

Scope đúng là:

```text
https://www.googleapis.com/auth/userinfo.email
```

### `curl` bị lỗi trên PowerShell

Trên PowerShell, dùng `curl.exe`, không dùng `curl`.

### Token test với Google thay đổi liên tục

Đây là bình thường. Access token mới được mint lại sẽ khác token cũ.

### Token hết hạn

Google access token sống ngắn, thường khoảng 1 giờ.
Theo tài liệu Socialdata, cookie `usession` thường có hạn khoảng 24 giờ.

## 11. Khi nào thì nhờ dev Socialdata

Sau khi đã check:

- email service account đã được add đúng
- scope đã là `userinfo.email`
- vẫn bị `Invalid Email`

thì chụp màn hình các thông tin sau gửi cho dev:

1. lệnh mint token
2. `service_account_email`
3. lệnh callback exchange
4. output lỗi đầy đủ

Không cần gửi private key.
