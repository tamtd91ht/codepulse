# IDEAS
- Deep review (2026-10-25): tầng hai cho diff lớn / chạm vùng rule, checkout repo ở `head_sha` vào thư mục tạm chỉ đọc và cho LLM dùng Read/Grep/Glob (không mạng, không ghi, ngân sách riêng) để đối chứng và loại false alarm như quy trình `/review-mr-dev` của omicx. Cần quyết định về bảo mật trước (hiện CLI chạy không tool để diff không thể prompt-inject).

- [DECISION] Review tự động push thẳng lên *nhánh review* của repo (Admin › Repo › Nhánh), không chỉ nhánh mặc định. Lợi: team làm trên develop được review không cần MR. Rủi ro: tốn LLM, rebase/force-push review lại cả loạt commit; nếu làm cần bỏ qua forced push và đặt trần mỗi ngày.
- (2026-10-27, từ phân tích luồng review, ngoài spec 19) Inline discussion trên MR chỉ cho finding `confirmed` critical/high, kèm bằng chứng của bước kiểm chứng; dispute nhiều lần trên cùng rule tự gợi ý sửa lời rule trong RK; model nhỏ (Haiku/Sonnet) cho bước chọn checks và biên dịch tầng tài liệu, Opus cho review và kiểm chứng; "convention probe" định kỳ so sánh code mới với `code_conventions` để báo drift không gắn với review nào.
- (2026-10-27) Khi dự án có nhiều repo cùng RK dùng chung, báo cáo tháng theo dự án: invariant nào bị vi phạm nhiều nhất, ở repo nào, xu hướng theo tuần.


- Chạy command `.claude` cần code của nhiều repo cùng lúc (clone thêm repo và `--add-dir`); hiện mỗi lần chạy chỉ một repo hoặc chỉ repo `.claude` dùng chung (người dùng 2026-10-30).
- Chạy command có MCP server (Jira, Sentry…) trên command worker: hiện `--strict-mcp-config` tắt mọi MCP.
- DPI (spec 25): nhận GitLab Code Quality report (CodeClimate JSON) / SonarQube cho R3 và complexity delta của B4; adapter Jira cho nguồn task; blame mức dòng cho rework 21 ngày (hiện tính mức file).

- Jira (spec 27): webhook Jira thay polling, tạo ticket Jira từ finding, đồng bộ hai chiều (CodePulse ghi lên Jira); hiện chỉ đọc theo JQL.

- Review qua MCP (spec 26 §22): yêu cầu review MR (`request_review` theo `iid`), tự gọi khi push qua git hook `post-push` (không có sẵn trong git; cần wrapper).
- Tệp quy tắc (spec 17 §1b): tệp quy tắc riêng từng repo (hiện chỉ cấp dự án; repo có file quy tắc trong repo).
- Tài liệu dự án (spec 29 §10): ảnh trong Word/PDF, Mermaid, tô màu code; Stetho đọc tài liệu dự án theo quyền dự án; bình luận và chủ sở hữu tài liệu, nhắc cập nhật tài liệu cũ; đồng bộ Confluence/Drive/`docs/` của repo; OCR PDF scan.
- Tài liệu dự án: khôi phục một phiên bản tải file lên hiện chép lại file gốc (≤ 20 MB) vào phiên bản mới; nên trỏ về file của phiên bản cũ thay vì chép (review 2026-11-11).
- Tài liệu dự án › Tổng hợp từ link (spec 29 §12.7): theo dõi link nguồn và đề xuất cập nhật khi trang đổi; đọc Confluence/Google Docs qua OAuth; tổng hợp từ cả thư mục `docs/` của repo.
