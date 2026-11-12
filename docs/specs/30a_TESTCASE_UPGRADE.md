# 30a – Testcase: spec nâng cấp lên ngang và vượt TestRail (nguồn của user)

> Bản chép tài liệu "CODEPULSE_TESTCASE_UPGRADE_SPEC.md" user gửi ngày 2026-11-12 (Google Docs).
> Hiện trạng và quyết định khi triển khai: `30_TESTCASES.md` §15. Khi hai tài liệu khác nhau, §15
> ghi lý do.

## 0. Quy tắc làm việc cho agent

1. Đọc code trước khi sửa; mục "[cần xác minh]" có thể đã có một phần.
2. Không phá API đang chạy: chỉ thêm trường / endpoint; đổi hành vi phải có version hoặc cờ.
3. Migration an toàn: trường mới nullable hoặc có mặc định; không mất dữ liệu; có rollback.
4. Giữ cơ chế revision (khoá lạc quan + restore); trường mới của testcase vào lịch sử revision.
5. Giữ quy ước giao diện: tiếng Việt, sáng/tối, nhãn ngắn, các tab hiện có.
6. Mỗi hạng mục một PR (commit), theo thứ tự §5; có test backend, test luồng chính, cập nhật trang
   Hướng dẫn và changelog.
7. Done: đạt acceptance criteria, không regression ở §1.3, phân quyền ở backend, cô lập theo
   business_project_id.

## 1.3 Luồng không được làm hỏng

- Tạo suite → thư mục lồng nhau → case dạng bước → sửa → khôi phục revision → xoá.
- Import Google Sheet → export Excel.
- Tạo run từ suite / thư mục / bộ lọc → ghi kết quả → đóng run → export.
- Plan theo cấu hình (mỗi tổ hợp một run).
- AI sinh case → apply.
- "Cần test lại": lọc MR theo ngày, nhánh → phân tích → tạo run.
- Thẻ thống kê: tổng / đã duyệt, % tự động, thiếu bước / kết quả, % issue Jira 90 ngày có testcase.

## 2. Khoảng trống so với TestRail

| # | Hạng mục | Ưu tiên |
|---|---|---|
| G1 | Nhận kết quả automation từ CI (JUnit, TestNG, xUnit, Playwright JSON, Cucumber JSON; map C{n} / automation_id / tự tạo case; CLI; flaky) | P0 |
| G2 | Liên kết Jira / code quy mô lớn (AI gợi ý jira_keys, code_paths theo lô; sinh case từ issue; code_paths cấp thư mục / suite, dạng {repo}:{glob}; ma trận truy vết) | P0 |
| G3 | Tạo bug Jira từ kết quả fail, link bug có sẵn, trạng thái bug, gợi ý retest khi bug Done | P0 |
| G4 | Báo cáo & dashboard (tổng kết run/plan, so sánh run, độ phủ, defect, năng suất, automation, sẵn sàng release Go/No-go; lọc; PDF/Excel/link; lịch gửi) | P0 |
| G5 | Trường tùy biến cho case và kết quả | P1 |
| G6 | Trạng thái tùy biến (+ Skipped), kết quả từng bước, giao việc, elapsed vs estimate, đính kèm / dán ảnh, phím tắt | P1 |
| G7 | Vai trò Admin dự án / Lead / Tester / Developer / Guest, ma trận quyền, quy trình duyệt | P1 |
| G8 | Template BDD (Gherkin, Scenario Outline, .feature) và Exploratory (charter, timebox, ghi chú theo thời gian) | P1 |
| G9 | Baseline / snapshot suite theo release, run từ baseline, so sánh baseline | P2 |
| G10 | Bulk edit mọi trường, copy/move, tìm-thay có xem trước, AI phát hiện trùng, Undo | P1 |
| G11 | Public API + API token (scope, dự án, hạn, thu hồi) + OpenAPI + webhook HMAC có retry | P0 |
| G12 | Audit log (trước/sau, lọc, export, ≥ 1 năm) | P2 |
| G13 | Tham số hoá / data-driven ({{biến}}, mỗi dòng tham số một test) | P2 |

## 3. Acceptance criteria (rút gọn, giữ đủ ý)

- **G11:** token chỉ `results:write` gọi POST /testcases → 403; token dự án A không đọc/ghi dự án B;
  thu hồi → 401; webhook lỗi 3 lần → failed, xem và gửi lại trên UI.
- **G1:** POST /testcases/runs/import-results (multipart; business_project_id, suite_id, run_name,
  milestone_id?, config_ids?, close_run?, mr_iid?, commit_sha?, branch?, run_id?, auto_create?).
  Map: C{n} trong tên/annotation (@C12, [C12]) → automation_id (duy nhất trong suite, khớp
  classname.name) → auto-create case kind=automated, draft, thư mục "Automation/Unmapped". Lưu
  elapsed, log lỗi, stack trace, đính kèm. "% tự động" = case automated có kết quả CI trong 30 ngày.
  Flaky: pass và fail trên cùng commit, hoặc đổi trạng thái ≥ 3 lần trong 10 lần gần nhất.
  AC: 50 test / 45 có C{n} → run 45 kết quả + báo 5 chưa map; upload lại với run_id → cập nhật,
  không trùng; 20 MB / 5 000 test ≤ 30 s chạy nền có tiến trình; có mr_iid → hiện ở "Cần test lại"
  của MR đó.
- **G2:** gán code_paths cho thư mục → MR khớp glob hiện các case trong thư mục; AI gợi ý cho 39 case
  → chấp nhận theo lô 1 thao tác, có revision; "% issue Jira 90 ngày có testcase" cập nhật ≤ 5 phút.
- **G3:** tạo bug từ kết quả fail → issue Jira đủ bước, link ngược, kết quả lưu mã bug; bug Done →
  case hiện "Cần retest" trong run đang mở.
- **G4:** "Sẵn sàng release" của milestone 5 000 kết quả ≤ 3 s; số liệu khớp dữ liệu gốc (có test).
- **G5:** trường bắt buộc chặn lưu ở UI và API; import Excel map tự động; xoá trường → lưu trữ,
  khôi phục được. Kiểu: text, textarea, số, ngày, dropdown, multi-select, checkbox, user, URL; bắt
  buộc, mặc định, phạm vi (dự án / suite / template), thứ tự; dùng ở lọc, cột, import/export, báo cáo,
  API, revision.
- **G6:** Failed ở bước 3 → kết quả Failed, ghi rõ bước lỗi; chế độ "chỉ người được giao".
- **G7:** Developer DELETE /testcases/{id} → 403; Tester đổi sang approved khi cờ duyệt bật → 403.
- **G8:** import .feature 3 scenario → 3 case BDD; export lại tương đương ngữ nghĩa; AI chuyển bước ⇄ BDD.
- **G9:** sửa case sau baseline → run từ baseline vẫn nội dung cũ.
- **G10:** sửa priority 200 case ≤ 5 s, đủ 200 revision; Undo khôi phục đúng.
- **G12:** ai làm gì, lúc nào, trước/sau cho suite, case, run, plan, cấu hình, quyền, token; lọc,
  export, lưu ≥ 1 năm.
- **G13:** bảng tham số; mỗi dòng sinh một test; bước dùng {{phone}}.

## 4. Vượt TestRail

| # | Tính năng |
|---|---|
| X1 | Sinh suite từ tài liệu dự án / link Drive: cây thư mục + case dạng bước, tag theo mã AC, link về BRD; báo cáo "AC chưa có case", "mâu thuẫn trong BRD" |
| X2 | BRD đổi version → diff yêu cầu → đánh dấu case "Cần cập nhật", AI đề xuất bản sửa |
| X3 | "Cần test lại" xếp hạng theo rủi ro: file đổi × lịch sử fail × ưu tiên × flaky × độ phủ; tạo run tối thiểu đủ an toàn |
| X4 | Bot comment MR: case cần chạy, kết quả CI đã map, case critical chưa chạy; tuỳ chọn chặn merge |
| X5 | AI chấm điểm chất lượng từng case và gợi ý sửa |
| X6 | AI phân tích fail từ log/stack trace: lỗi sản phẩm / test / môi trường / flaky; gợi ý MR gây lỗi |
| X7 | Sinh khung code automation (Playwright / REST-assured) có annotation C{n} |
| X8 | Stetho hỏi đáp về testcase / run |
| X9 | MCP tools: list/create/update case, tạo run, ghi kết quả |

## 5. Lộ trình

1. G11 → G1 → G2 · 2. G3, G4, G6 · 3. G5, G7, G10 · 4. G8, G9, G12, G13 · 5. X1–X9.

## 6. Dữ liệu mẫu

OMICX-BE (business_project_id = 8), suite 3 "CSKH › Khách hàng – Thêm / Sửa / Xóa": 39 case, 7 thư
mục; BRD_KhachHang_ThemXoaSua; repo vihat-saas/omicx/service/cloud-saas-omicx-customer-service.
