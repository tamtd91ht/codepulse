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

## Checklist
- [ ] CLI `codepulse identity import --teams --users --accounts`
- [ ] API CRUD users/teams/accounts + endpoint link/unlink account
- [ ] Lịch sử team theo thời gian (`joined_at`/`left_at`) – báo cáo tháng dùng team tại thời điểm commit
- [ ] Bot accounts (renovate, dependabot, CI) → đánh dấu `users.active=false` & loại khỏi báo cáo
