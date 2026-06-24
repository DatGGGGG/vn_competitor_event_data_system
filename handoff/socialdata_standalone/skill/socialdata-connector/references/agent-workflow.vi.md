# Workflow cho agent

## Muc tieu

Ket noi Socialdata ma khong can repo goc.

## Inputs toi thieu

- duong dan file Google service-account JSON
- URL Socialdata cua user de suy ra slug

## Cach lay slug

Khong duoc mac dinh `srcvn`.

Hay lay slug tu URL.

Vi du:

```text
https://socialdata.garena.vn/srcvn/member/channel
```

thi slug la `srcvn`.

Noi chung, lay phan nam ngay sau domain `socialdata.garena.vn/`.

## Thu tu nen chay

### 1. In email service account ra de user kiem tra

Yeu cau user cung cap file JSON key local.

### 1.1 Bao user check man `Users`

User can mo:

```text
https://socialdata.garena.vn/<slug>/moderator/user
```

hoac bam menu `Users` trong giao dien.

Neu email service account chua ton tai, user can bam `Add` va dien:

- `Name`
- `Email`
- `Role`

`Email` phai bang `client_email` trong file JSON key.

### 2. Mint token

```powershell
node .\socialdata_mint_google_access_token.mjs `
  --key-file C:\path\to\service-account.json
```

### 3. Exchange callback

```powershell
$token = node .\socialdata_mint_google_access_token.mjs `
  --key-file C:\path\to\service-account.json `
  --token-only
$token = $token.Trim()
curl.exe -i --max-redirs 0 "https://socialdata.garena.vn/connect/google/callback?access_token=$token"
```

### 4. Test GraphQL

```powershell
curl.exe --location "https://socialdata.garena.vn/graphql" `
  --header "cookie: usession=PASTE_USESSION_HERE" `
  --header "content-type: application/json" `
  --data "{\"query\":\"query { __typename }\",\"variables\":{}}"
```

## Dinh nghia thanh cong

- callback tra `302`
- `Set-Cookie` co `usession`
- GraphQL tra ve `__typename`

## Loi thuong gap

### `Invalid Email`

Thuong do:

- email service account chua duoc add vao Socialdata
- scope mint token sai

Scope dung:

```text
https://www.googleapis.com/auth/userinfo.email
```
