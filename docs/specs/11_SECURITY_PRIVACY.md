# 11 – Security & Privacy

## Bảo mật kỹ thuật
- [ ] Token GitLab & webhook secret mã hoá at-rest (Fernet), không log ra
- [ ] Webhook: xác thực `X-Gitlab-Token`, giới hạn IP nguồn (tuỳ chọn), chống replay bằng Event UUID
- [ ] Token GitLab dùng tài khoản bot riêng, scope tối thiểu (`read_api`; `api` chỉ khi đăng comment)
- [ ] HTTPS bắt buộc; CORS chỉ domain dashboard
- [ ] Dependency scan (`pip-audit`) trong CI

## Dữ liệu gửi cho LLM
- [ ] `[DECISION]` Được phép gửi code ra LLM bên ngoài không? Nếu có: dùng gói API cam kết không train trên dữ liệu
- [ ] Cờ `projects.allow_external_llm=false` → bắt buộc dùng provider local
- [ ] Trước khi gửi: chạy Gitleaks/regex để **che secret** trong diff (thay bằng `***REDACTED***`)
- [ ] Không gửi file trong `SENSITIVE_PATHS` (vd: `**/secrets/**`, `.env*`, `*.pem`)

## Quyền riêng tư & công bằng (quan trọng khi đánh giá người)
- [ ] Thông báo minh bạch cho team: tool đo gì, dùng vào việc gì
- [ ] Developer xem được toàn bộ dữ liệu về mình + quyền dispute
- [ ] Không công khai bảng xếp hạng cá nhân cho toàn team (chỉ lead) – `[DECISION]`
- [ ] Điểm là **tín hiệu coaching**, không phải căn cứ duy nhất cho đánh giá hiệu suất (ghi rõ trên dashboard)
- [ ] Data retention: xoá `code_excerpt` sau `RETENTION_DAYS` (mặc định 365), giữ metric tổng hợp
- [ ] Audit log truy cập dữ liệu cá nhân
