# Bat dau tu day

Bo nay dung de gui cho nguoi khac ma khong can clone repo `vn_competitor_event_data_system`.

Muc tieu cua bo nay:

- nguoi dung khong can biet code
- nguoi dung khong can tu go CLI
- Claude/Codex se lam toan bo phan ky thuat

## Nguoi dung chi can lam 4 viec

### Viec 1: Lay dung `slug` cua team minh

Mo trang Socialdata cua minh tren trinh duyet.

Nhin len thanh dia chi.

Vi du:

```text
https://socialdata.garena.vn/srcvn/member/channel
```

thi `slug` la:

```text
srcvn
```

Noi chung:

- `slug` la doan ngay sau `socialdata.garena.vn/`
- khong duoc mac dinh dung `srcvn`
- moi team co the co slug khac nhau

### Viec 2: Tao Google service account va tai file JSON key

1. Mo:
   [https://console.cloud.google.com/apis/credentials](https://console.cloud.google.com/apis/credentials)
2. Chon dung Google Cloud project
3. Bam `Create credentials`
4. Chon `Service account`
5. Dat ten bat ky, vi du `socialdata-reader`
6. Bam `Create and continue`
7. Neu khong biet chon role nao trong Google Cloud thi co the bam tiep va `Done`
8. Mo service account vua tao
9. Vao tab `Keys`
10. Bam `Add key` -> `Create new key`
11. Chon `JSON`
12. Bam `Create`
13. Trinh duyet se tai ve 1 file `.json`

Luu y:

- khong gui noi dung file JSON vao chat
- khong commit file JSON len git

### Viec 3: Add email do vao Socialdata

1. Trong Socialdata, vao menu `Users`
2. Bam nut `Add`
3. Form `Add new User` hien ra
4. Dien:
   - `Name`: dat ten de nhan biet, vi du `socialdata-reader`
   - `Email`: email service account trong file JSON key
   - `Role`: chon role phu hop, thuong la `STAFF` neu team khong co quy dinh khac
5. Bam `Submit`

Luu y:

- `Email` o day phai la email service account trong file JSON
- neu add sai hoac chua add, Socialdata thuong se bao loi `Invalid Email`

### Viec 4: Dua folder nay + file JSON + URL Socialdata cho Claude/Codex

Nguoi dung chi can noi voi Claude/Codex 3 thong tin:

1. URL Socialdata cua minh
2. duong dan den file JSON key
3. folder handoff nay

Tat ca phan con lai de Claude/Codex lam.

## Khong can tu go lenh

Neu nguoi dung khong biet:

- PowerShell la gi
- CLI la gi
- Node la gi

thi cung khong sao.

Claude/Codex se tu huong dan tiep hoac tu chay phan ky thuat, neu moi truong cua ho cho phep.

## Prompt de dua cho Claude/Codex

Mo file:

- `claude_prompt_vi.md`

va dua nguyen noi dung file do cho Claude/Codex.

## Claude/Codex se lam nhung gi

Sau khi nhan prompt, agent se:

1. doc `slug` tu URL cua user
2. kiem tra email service account tu file JSON
3. nhac user add email vao Socialdata neu can
4. cai dependency can thiet
5. mint Google access token
6. exchange token sang `usession`
7. test GraphQL
8. neu can, introspect schema hoac tim query post/channel

## Neu nguoi dung can gui nhanh cho teammate

Co the gui nguyen van doan nay:

```text
Em chi can lam 4 viec:
1. Mo Socialdata va chup/nhin URL de lay slug cua team em
2. Tao Google service account va tai file JSON key
3. Vao Socialdata > Users > Add, add email service account do vao
4. Dua folder socialdata_standalone + duong dan file JSON + URL Socialdata cua em cho Claude/Codex

Con lai Claude/Codex se lo phan ky thuat.
```
