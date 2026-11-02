# 05 – Identity & Team Mapping

## Vấn đề
Một người có nhiều email Git (công ty, cá nhân, máy khác) và một GitLab user. Muốn điểm tính đúng **người**, không phải email.

## Quy tắc tự động
- [ ] Mỗi GitLab user → 1 `accounts(kind='gitlab_user')`.
- [ ] Mỗi email tác giả commit → 1 `accounts(kind='git_email')`, chuẩn hoá lowercase.
- [ ] Auto-link email → user nếu: trùng `primary_email`, hoặc trùng email public của GitLab user, hoặc khớp `ALLOWED_EMAIL_DOMAINS` + username.
- [ ] Email `noreply` của GitLab (`<id>-<username>@users.noreply.gitlab.com`) → parse ra username để link.
- [ ] Account chưa link → hiển thị trong trang "Unmapped accounts" cho admin.

## Import CSV (giống DevLake Team Configuration)
`teams.csv`
```csv
team_name,parent_team
Backend,
Frontend,
Mobile,
```
`users.csv`
```csv
full_name,primary_email,team_name,role,joined_at
Nguyen Van A,a@company.vn,Backend,member,2025-01-01
```
`accounts.csv`
```csv
kind,external_key,primary_email
git_email,a.personal@gmail.com,a@company.vn
gitlab_user,1234567,a@company.vn
```

## Gắn tác giả từ danh sách kết quả review (2026-11-02)
- Dòng review chưa có người (`author_user_id` null) hiện tác giả gốc: tên + email git (commit) hoặc
  `GitLab @username` (MR) — `ReviewListItem.author_account`. Tài khoản đã gắn người sau khi review chạy
  thì ghi "Đã gắn X, chưa đồng bộ".
- Ai gắn được: admin, hoặc người quản lý (`manage`) dự án của review. Người quản lý chỉ gắn với người
  của dự án (được cấp quyền, hoặc đã commit trong dự án 90 ngày qua), không tạo người mới, không đổi
  tài khoản đã thuộc người khác (409, việc của admin). Admin gắn với bất kỳ ai và tạo được người mới từ
  email git.
- `GET /reviews/{id}/author` (tác giả, người chọn được, số review cũ chưa có người),
  `POST /reviews/{id}/author {user_id | create, sync}`. Gắn theo **đúng tài khoản đó** (một email git /
  một tài khoản GitLab), không gộp theo tên.
- Sau khi chọn người, giao diện hỏi **"Đồng bộ dữ liệu cũ?"**: **Có** → `sync=true`: mọi review của
  tài khoản lấy người này (`attribute_reviews(unassigned_only=False)`) và các tuần/tháng liên quan được
  tính lại ở nền (`aggregate_periods_job`); **Không** → chỉ gắn, review cũ giữ nguyên, dòng đó còn nút
  **Đồng bộ** để làm sau. Audit `account.link` kèm `via=review:<id>`, `backfill`.

## Checklist
- [ ] CLI `codepulse identity import --teams --users --accounts`
- [ ] API CRUD users/teams/accounts + endpoint link/unlink account
- [ ] Lịch sử team theo thời gian (`joined_at`/`left_at`) – báo cáo tháng dùng team tại thời điểm commit
- [ ] Bot accounts (renovate, dependabot, CI) → đánh dấu `users.active=false` & loại khỏi báo cáo
