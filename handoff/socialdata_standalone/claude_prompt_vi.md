# Prompt san cho Claude/Codex

Paste nguyen prompt duoi day cho Claude/Codex:

```text
Ban dang lam viec trong mot bo handoff Socialdata standalone.

Muc tieu:
- ket noi vao Socialdata ma khong can repo warehouse goc
- reuse cac file co san trong folder hien tai
- khong viet lai auth flow tu dau
- giam toi da so viec ma nguoi dung khong ky thuat phai tu lam

Input quan trong:
- user se dua cho ban:
  - URL Socialdata cua ho
  - duong dan file Google service-account JSON tren may ho
  - folder handoff nay
- hay coi user la nguoi khong biet code va khong quen PowerShell/CLI

Cach lay slug:
- neu URL la `https://socialdata.garena.vn/srcvn/member/channel` thi slug la `srcvn`
- noi chung, lay phan nam sau domain `socialdata.garena.vn/` va truoc dau `/` tiep theo

Bat buoc:
1. Doc skill tai ./skill/socialdata-connector
2. Dung script ./socialdata_mint_google_access_token.mjs de mint Google token
3. Scope mac dinh phai la https://www.googleapis.com/auth/userinfo.email
4. Exchange token qua GET https://socialdata.garena.vn/connect/google/callback?access_token=<token>
5. Parse usession tu header Set-Cookie
6. Dung cookie usession de goi GraphQL

Thu tu thuc hien:
1. Xac nhan slug tu URL Socialdata cua user
2. Kiem tra file JSON key va in ra client_email
3. Kiem tra xem user da add email do vao man `Users` cua Socialdata chua
4. Neu chua, huong dan user bang tieng Viet rat don gian, tung buoc mot, chi dung nhung viec tren giao dien web ma user bat buoc phai tu lam
5. Tu ban xu ly toan bo phan technical con lai
6. Neu can dependency de chay script, hay noi ro user can bam lenh nao, nhung uu tien giai thich rat don gian
7. Mint token
8. Test callback exchange
9. Test GraphQL query: query { __typename }
10. Neu user can, introspect schema hoac tim query post/channel

Khong duoc:
- dung cloud-platform lam scope duy nhat
- yeu cau user paste private key vao chat
- tu phat minh auth flow khac
- day nguoi dung vao cac khai niem technical neu khong can thiet

Neu loi:
- bao cao ro command da chay
- output loi
- nguyen nhan nghi ngo
- de xuat buoc tiep theo

Phong cach tra loi:
- uu tien tieng Viet
- huong dan user rat ngan gon
- neu user phai tu lam gi do trong browser, viet theo kieu "bam vao dau", "dien gi vao o nao"
- khong gia dinh user biet PowerShell, CLI, JSON, env var, hay dependency manager
```
