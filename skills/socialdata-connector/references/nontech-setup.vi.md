# Tóm tắt cho người không kỹ thuật

Dùng file này khi cần hướng dẫn một đồng nghiệp không biết code.

## Hai việc người dùng bắt buộc phải tự làm

1. Tạo Google service account và tải file JSON key
2. Add email service account vào trang `Users` của Socialdata

## Điều agent nên nhắc người dùng

- không gửi private key vào chat
- chỉ đưa đường dẫn file JSON local
- dùng `curl.exe` nếu đang ở PowerShell
- token Google sống ngắn, thường khoảng 1 giờ
- `usession` của Socialdata theo tài liệu thường sống khoảng 24 giờ

## Câu trả lời ngắn cho những câu hỏi phổ biến

### `Tạo service account ở đâu?`

`https://console.cloud.google.com/apis/credentials`

### `Lưu file key ở đâu?`

`local_secrets/` trong repo

### `Cần gửi gì cho Claude/Codex?`

- đường dẫn repo
- đường dẫn file JSON
- app slug `srcvn`
- mục tiêu công việc

### `Khi nào biết đã thành công?`

Khi `socialdata-auth-check` trả về:

```json
{
  "data": {
    "__typename": "Query"
  }
}
```
