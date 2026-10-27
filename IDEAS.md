# IDEAS
- Deep review (2026-10-25): tầng hai cho diff lớn / chạm vùng rule, checkout repo ở `head_sha` vào thư mục tạm chỉ đọc và cho LLM dùng Read/Grep/Glob (không mạng, không ghi, ngân sách riêng) để đối chứng và loại false alarm như quy trình `/review-mr-dev` của omicx. Cần quyết định về bảo mật trước (hiện CLI chạy không tool để diff không thể prompt-inject).

- [DECISION] Review tự động push thẳng lên *nhánh review* của repo (Admin › Repo › Nhánh), không chỉ nhánh mặc định. Lợi: team làm trên develop được review không cần MR. Rủi ro: tốn LLM, rebase/force-push review lại cả loạt commit; nếu làm cần bỏ qua forced push và đặt trần mỗi ngày.
- (2026-10-27, từ phân tích luồng review, ngoài spec 19) Inline discussion trên MR chỉ cho finding `confirmed` critical/high, kèm bằng chứng của bước kiểm chứng; dispute nhiều lần trên cùng rule tự gợi ý sửa lời rule trong RK; model nhỏ (Haiku/Sonnet) cho bước chọn checks và biên dịch tầng tài liệu, Opus cho review và kiểm chứng; "convention probe" định kỳ so sánh code mới với `code_conventions` để báo drift không gắn với review nào.
- (2026-10-27) Khi dự án có nhiều repo cùng RK dùng chung, báo cáo tháng theo dự án: invariant nào bị vi phạm nhiều nhất, ở repo nào, xu hướng theo tuần.

