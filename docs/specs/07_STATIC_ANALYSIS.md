# 07 – Static Analysis bổ trợ (Tuỳ chọn – Phase 4)

AI review có thể bỏ sót hoặc dao động. Kết hợp công cụ tĩnh cho kết quả ổn định, có thể tái lập.

## Lựa chọn
| Công cụ | Cách tích hợp | Map severity |
|---|---|---|
| **Semgrep** (khuyến nghị bắt đầu) | Worker chạy `semgrep --json` trên các file thay đổi | ERROR→high, WARNING→medium, INFO→low; rule bảo mật mức ERROR→critical |
| **SonarQube** (nếu đã có) | Đọc API `/api/issues/search?componentKeys=&createdAfter=` (giống DevLake plugin SonarQube) | BLOCKER→critical, CRITICAL→high, MAJOR→medium, MINOR→low, INFO→info |
| **Gitleaks** | Quét secret trong diff | mọi phát hiện → critical |
| GitLab Code Quality report | Đọc artifact `gl-code-quality-report.json` từ pipeline | theo field `severity` |

## Checklist
- [ ] Interface `StaticAnalyzer.run(project, sha, files) -> list[Finding]`
- [ ] Ghi vào `review_findings` với `source='semgrep'|'sonar'|'gitleaks'`
- [ ] Dedupe với finding AI qua `fingerprint` (cùng file + dòng ± 3 + category)
- [ ] Cấu hình bật/tắt theo project
