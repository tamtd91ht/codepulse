# Pilot Engineering Insights & hồ sơ tháng (docs/specs/20 §12, task 9.7)

Mục tiêu: trong **8 tuần**, với **2–3 team**, kiểm chứng ba điều trước khi mở cho cả công ty:
1. Số liệu cấp team (Luồng làm việc, Tải review, DORA, Rủi ro tri thức) **khớp cảm nhận** của lead và được dùng để ra quyết định cải tiến.
2. Hồ sơ tháng giúp buổi 1:1 **ngắn hơn và cụ thể hơn**, không làm dev lo bị chấm điểm.
3. Không có số liệu sai mà không truy được nguồn.

Không phải mục tiêu: xếp hạng người, gắn lương thưởng (D1).

---

## 1. Chọn team (tuần 0)

Chọn 2–3 team **khác kiểu việc**, để thấy chỗ số liệu lệch theo bối cảnh (§11 của DPI, §1.3 của spec 20):

| Team | Kiểu việc | Vì sao cần |
|---|---|---|
| A | Phát triển tính năng (nhiều MR vừa và nhỏ) | Trường hợp chuẩn của chỉ số luồng |
| B | Backend / core (MR lớn, module cũ, ít người nắm) | Thử Rủi ro tri thức, MR XL, chờ review |
| C (tuỳ chọn) | Vận hành / hạ tầng (deploy nhiều, sự cố) | Thử DORA và incident |

Điều kiện: lead đồng ý tham gia; team đã dùng MR (không push thẳng là chính); repo đã gom vào dự án trong CodePulse.

## 2. Chuẩn bị kỹ thuật (tuần 0, admin)

- [ ] Repo của các team đã `tracked`, sync xong (Admin › Dự án & Repo).
- [ ] Chạy `codepulse insights backfill-files` (dựng `commit_files` từ dữ liệu đã có) để tab Rủi ro tri thức có lịch sử.
- [ ] Token GitLab của CodePulse có quyền **Reporter** trở lên trên các repo (đọc deployments, issues).
- [ ] DORA: mở tab Triển khai (DORA), kiểm tra cột "Môi trường production" của từng repo; repo dùng tên lạ thì admin đặt tay (CodePulse tự đồng bộ lại).
- [ ] Sự cố: thống nhất với team gắn nhãn `incident` (hoặc dùng issue loại Incident) cho mọi sự cố production. Nhãn khác thì đặt `INCIDENT_LABELS`.
- [ ] Người & Team: mọi thành viên có trong team, đúng vai trò `lead`/`member`; tài khoản Git đã gắn người (Admin › Tài khoản chưa gắn người).
- [ ] Nếu dùng bản nháp AI: CodePulse đã kết nối Claude (Admin › LLM & Chi phí), ngân sách tháng còn đủ; repo nào không được gửi dữ liệu ra LLM thì để `allow_external_llm = false`.

## 3. Truyền thông với team (tuần 0, lead đọc cho team)

> Từ tuần này team mình thử hai thứ trên CodePulse.
> **Luồng làm việc** cho thấy công việc chảy qua team: MR chờ review bao lâu, MR to hay nhỏ, ai đang gánh review, module nào chỉ một người nắm, deploy và sự cố. Đây là số liệu **của team** để mình sửa quy trình, không có điểm cho từng người.
> **Hồ sơ tháng** là trang chuẩn bị cho buổi 1:1: hệ thống gom việc bạn đã làm, bạn bổ sung việc ngoài Git, mình nhận xét có dẫn chứng, hai bên chốt mục tiêu. Chỉ bạn, mình (lead) và admin xem được. **Không có điểm và không dùng để tính lương thưởng.**
> Thấy số nào sai hay không công bằng, báo mình: mình sẽ truy tới dữ liệu gốc và sửa.

Gửi kèm `docs/USER_GUIDE.md` mục 6 và 6a.

## 4. Nhịp hằng tuần (tuần 1–8)

| Khi | Ai | Việc |
|---|---|---|
| Đầu tuần, 15 phút trong họp team | Lead | Mở **Luồng làm việc** tuần trước: MR chờ phản hồi lâu nhất, tỉ lệ merge không review, MR XL. Chọn **một** việc cải tiến (ví dụ: ai rảnh xem MR chờ > 1 ngày; tách MR > 1000 dòng) |
| Giữa tuần | Lead | Xem **Tải review**: nếu một người gánh > 60% thì chia lại |
| Cuối mỗi 2 tuần | Lead | Xem **Rủi ro tri thức**: module "Một người nắm" quan trọng thì sắp người thứ hai cùng làm/review |
| Ngày 1–3 hằng tháng | Dev | Mở **Hồ sơ tháng** tháng trước (đã có nháp), bổ sung việc ngoài Git, tự nhận xét, **Gửi cho lead** |
| Trong tuần đầu tháng | Lead | Đọc hồ sơ, (tuỳ chọn) tạo bản nháp AI rồi sửa, viết nhận xét có dẫn chứng, **Đã xem**; họp 1:1, ghi mục tiêu, **Chốt** |
| Mỗi tuần | Admin | Ghi lại mọi phản ánh "số liệu sai" vào bảng theo dõi (mục 6) và truy nguyên |

## 5. Đo kết quả (theo §12 của spec 20)

| Tiêu chí | Cách đo | Ngưỡng để mở rộng |
|---|---|---|
| Lead dùng số liệu khi họp cải tiến | Lead ghi lại mỗi lần dùng (mục 6) | ≥ 2 lần / tháng / team |
| Có hành động cụ thể từ số liệu | Ghi hành động + số liệu dẫn tới nó | ≥ 1 / team |
| Quy trình tốt lên | So tuần 1–2 với tuần 7–8: trung vị **Chờ review**, **Merge không review**, **MR XL** | Ít nhất một chỉ số tốt lên rõ |
| Hồ sơ tháng được dùng | Số hồ sơ **Đã chốt** / số người | ≥ 80% ở tháng thứ 2 |
| Niềm tin của dev | Khảo sát mục 7, cuối tuần 4 và tuần 8 | ≥ 70% đồng ý câu 1 và câu 2 |
| Số liệu sai được xử lý | Mọi phản ánh có nguyên nhân và cách sửa | 100% phản ánh có kết luận |

## 6. Bảng theo dõi (một bảng chung cho pilot)

| Ngày | Team | Loại (dùng số liệu / hành động / phản ánh sai) | Mô tả | Số liệu / link | Kết luận |
|---|---|---|---|---|---|

## 7. Khảo sát dev (ẩn danh, 5 phút, tuần 4 và tuần 8)

Thang 1–5 (1 = rất không đồng ý, 5 = rất đồng ý):
1. Số liệu Luồng làm việc phản ánh đúng cách team mình làm việc.
2. Tôi không lo bị đánh giá sai vì CodePulse.
3. Hồ sơ tháng giúp buổi 1:1 của tôi cụ thể hơn.
4. Nhận xét của lead trong hồ sơ có dẫn chứng rõ ràng.
5. Phần tự bổ sung việc ngoài Git là đủ để việc của tôi được ghi nhận.
6. (Mở) Số liệu nào bạn thấy sai hoặc không công bằng? Vì sao?
7. (Mở) Bạn muốn bỏ đi hoặc thêm gì?

Khảo sát lead (tuần 8), thang 1–5: số liệu giúp phát hiện điểm nghẽn; hồ sơ tháng tiết kiệm thời gian chuẩn bị 1:1; bản nháp AI dùng được sau khi sửa ít; (mở) thiếu gì để dùng hằng tuần.

## 8. Quyết định cuối pilot (tuần 8)

- **Mở rộng** khi đạt ngưỡng mục 5: thêm 3–5 team mỗi đợt, lặp lại mục 2–3.
- **Sửa rồi pilot tiếp 4 tuần** khi có chỉ số bị phản ánh sai nhiều lần: sửa định nghĩa trong spec 20 (tăng `INSIGHTS_VERSION`), ghi vào PROGRESS.
- **Dừng phần nào** dev đánh giá dưới 3/5 ở câu 2 (lo bị đánh giá sai): giữ số liệu cấp team, tạm ẩn phần cá nhân cho tới khi tìm ra nguyên nhân.

Mọi thay đổi định nghĩa sau pilot ghi vào spec 20 và PROGRESS.md; không dùng số liệu pilot cho lương thưởng (D1).
