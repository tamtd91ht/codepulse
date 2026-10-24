# IDEAS
- Deep review (2026-10-25): tầng hai cho diff lớn / chạm vùng rule, checkout repo ở `head_sha` vào thư mục tạm chỉ đọc và cho LLM dùng Read/Grep/Glob (không mạng, không ghi, ngân sách riêng) để đối chứng và loại false alarm như quy trình `/review-mr-dev` của omicx. Cần quyết định về bảo mật trước (hiện CLI chạy không tool để diff không thể prompt-inject).
