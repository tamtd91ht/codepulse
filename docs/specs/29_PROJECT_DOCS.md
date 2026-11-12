# 29 – Tài liệu dự án (module Document)

> Ngoài spec gốc, theo yêu cầu người dùng (2026-11-11): "cho phép add tài liệu hệ thống lên, tức là
> tạo 1 module document để người dùng cập nhật tài liệu dự án lên, chỗ này hãy đề xuất 1 phương án
> tối ưu nhất về UI UX và render sau khi người dùng tải document lên. Làm kỹ nội dung tài liệu này
> nhé." Người dùng giao toàn quyền quyết định; mọi mục **[Quyết định]** do agent chọn theo góc nhìn
> quản lý, người dùng xem lại khi duyệt trên web.

## 1. Bài toán và mục tiêu

Tài liệu hệ thống hiện nằm rải rác (Confluence, Google Drive, file Word gửi qua chat, README trong
từng repo). Hệ quả: người mới mất nhiều ngày hỏi lại; tài liệu cũ không ai biết đã sai; khi một
người nghỉ, tri thức đi theo. CodePulse đã là nơi team vào hằng ngày (review, finding, DPI), nên
đặt tài liệu dự án ở đây:

| Mục tiêu | Đo bằng |
|---|---|
| Ai trong dự án cũng tìm ra tài liệu trong < 30 giây | tìm kiếm không dấu, theo mục, ngay trên trang |
| Đọc dễ như một trang docs, không phải mở file Word | hiển thị Markdown đầy đủ, mục lục bám cuộn |
| Đưa tài liệu cũ lên không tốn công chuyển đổi | tải nhiều file Word/PDF/Markdown/Excel cùng lúc |
| Biết dự án thiếu tài liệu gì, tài liệu nào đã cũ | thẻ "Độ đầy đủ" 7 nhóm, cờ "có thể đã cũ" > 180 ngày |
| Không mất lịch sử, sửa nhầm khôi phục được | mỗi lần lưu là một phiên bản, so sánh và khôi phục |
| Viết tài liệu mới đúng cấu trúc | 8 mẫu soạn sẵn có câu hỏi định hướng và chỗ `<…>` để điền |

Không làm (xem §10): soạn thảo cộng tác thời gian thực, bình luận trên tài liệu, phân quyền từng
tài liệu, OCR file scan.

## 2. Các phương án đã cân nhắc **[Quyết định]**

| Phương án | Ưu | Nhược | Kết luận |
|---|---|---|---|
| A. Chỉ lưu file, bấm vào thì tải về / mở bằng ứng dụng ngoài | Đơn giản | Không tìm được nội dung, không đọc trên web, Word phải tải về | Loại |
| B. Lưu file + nhúng trình xem của từng định dạng (Office viewer, PDF.js) | Giữ y nguyên định dạng | Office viewer cần gửi file ra Microsoft (lộ tài liệu nội bộ); PDF.js nặng; không tìm toàn văn, không mục lục chung | Loại |
| C. **Chuyển mọi file thành Markdown để hiển thị + tìm kiếm, giữ file gốc để tải về; PDF thêm chế độ xem bản gốc bằng trình xem PDF của trình duyệt** | Một cách đọc thống nhất, tìm được, có mục lục, sửa được trên web, không gửi file ra ngoài | Mất ảnh và định dạng phức tạp của Word | **Chọn** |
| D. Tích hợp Confluence/Drive (đọc qua API) | Không trùng nơi lưu | Mỗi công ty một hệ, cần thêm tài khoản dịch vụ và quyền | Để sau (IDEAS) |

Lý do chọn C: người đọc cần **nội dung** và **tìm được**, không cần đúng phông chữ; người viết cần
**sửa nhanh** trên web. Bản gốc vẫn tải về được nên không mất gì so với A. PDF (thường là tài liệu
từ đối tác, có bố cục quan trọng) có thêm chế độ "Bản gốc PDF".

## 3. Mô hình dữ liệu (migration `d2f4a6b8c0e2`)

- `project_documents`: `id`, `business_project_id` (FK cascade), `title`, `category`, `summary`,
  `position`, `pinned`, `archived_at`, `current_version_id`, `version_count`, `created_by`,
  `updated_by`, timestamps.
- `project_document_versions`: `id`, `document_id` (FK cascade), `number` (1, 2, …; unique theo
  tài liệu), `body` (Markdown), `chars`, `content_hash`, `source` (`upload | editor | template |
  restore`), `note` (≤ 300), `file_name`, `file_kind`, `file_size`, `original` (bytea, file gốc
  ≤ 20 MB, chỉ có với `upload`/`restore` của upload), `meta` (JSONB: `pages`, `sheets`, `plain`,
  `template`), `created_by`, `created_at`.
- `project_document_chunks`: các mục của **phiên bản hiện tại**, mỗi mục ~1.200 ký tự dưới đường
  dẫn tiêu đề ("Mục > Mục con"), `search` = `to_tsvector('simple', <tiêu đề tài liệu + tiêu đề mục
  + nội dung, bỏ dấu>)`, chỉ mục GIN.
- **[Quyết định]** Nhóm (`category`) cố định 9 giá trị, không cho tạo nhóm tự do: `overview`
  (Tổng quan), `architecture` (Kiến trúc), `setup` (Cài đặt & môi trường), `api` (API & tích hợp),
  `deploy` (Triển khai), `runbook` (Vận hành & sự cố), `conventions` (Quy ước & quy trình),
  `decision` (Quyết định kỹ thuật – ADR), `other` (Khác). Nhóm cố định giúp mọi dự án có cùng cấu
  trúc, người chuyển dự án vẫn biết tìm ở đâu, và đo được độ đầy đủ.
- Không bao giờ ghi đè phiên bản; khôi phục = tạo phiên bản mới có nội dung (và file gốc) của bản
  cũ. Xoá vĩnh viễn chỉ áp cho tài liệu đã lưu trữ (2 bước, tránh xoá nhầm).

## 4. Quyền **[Quyết định]**

| Quyền | Loại | Mặc định | Ý nghĩa |
|---|---|---|---|
| `docs.view` | cờ | mọi thành viên (bộ "Thành viên") | Xem tài liệu của các dự án **mình được xem** (spec 09); dự án khác không tồn tại với họ (404) |
| `docs.edit` | phạm vi `project` / `all` | "Quản lý dự án" = `project`, "Quản lý tổng" = `all`, admin | Thêm, tải lên, sửa, tải phiên bản mới, khôi phục, ghim, đổi nhóm, lưu trữ, xoá |

Lead muốn sửa tài liệu: admin gán bộ quyền có `docs.edit` (Admin › Phân quyền). Mọi thao tác ghi
có audit `doc.create | doc.upload | doc.version | doc.edit | doc.restore | doc.update | doc.delete`.

## 5. UI/UX **[Quyết định]**

### 5.1 Bố cục: cổng tài liệu 3 cột (kiểu GitBook / Docusaurus)

```text
┌ Thanh bên (280px) ─────┐ ┌ Nội dung ──────────────────────────────┐ ┌ Mục lục (220px) ┐
│ Dự án [select ▾]       │ │ Dự án › Kiến trúc                       │ │ TRÊN TRANG NÀY  │
│ [🔍 Tìm (phím /)     ] │ │ Kiến trúc thanh toán     [⌕][⟲][⤓][✎] │ │ Bối cảnh        │
│ Độ đầy đủ 5/7 ▓▓▓▓░░   │ │ Mô tả ngắn                              │ │ ▸ Thành phần    │
│ ĐÃ GHIM                │ │ Phiên bản 4 · 2 ngày trước bởi An ·     │ │   Luồng chính   │
│  · Tổng quan dự án     │ │ kien-truc.docx · Word · ~6 phút đọc     │ │   Dữ liệu       │
│ KIẾN TRÚC          2   │ │ ─────────────────────────────────────── │ │                 │
│  · Kiến trúc tt     ●  │ │ ## Bối cảnh                             │ │                 │
│ TRIỂN KHAI         1   │ │ …                                       │ │                 │
└────────────────────────┘ └─────────────────────────────────────────┘ └─────────────────┘
```

- **Thanh bên**: chọn dự án (nhớ dự án dùng lần trước trong trình duyệt); ô tìm kiếm (phím `/`);
  thẻ **Độ đầy đủ** (x/7 nhóm cốt lõi, mở ra thấy nhóm nào còn thiếu); cây tài liệu theo nhóm, nhóm
  **Đã ghim** lên đầu; chấm vàng = "có thể đã cũ"; công tắc "Xem tài liệu đã lưu trữ".
- **Nội dung**: breadcrumb, tiêu đề, mô tả, dòng thông tin (phiên bản, ai cập nhật lúc nào, file
  gốc + loại + dung lượng + số trang, thời gian đọc), thanh thao tác bằng icon có nhãn (tìm trong
  tài liệu, lịch sử, tải file gốc, in/lưu PDF, ghim, đổi tên/nhóm, tải phiên bản mới, **Sửa**).
- **Mục lục** "Trên trang này": tự sinh từ tiêu đề, đánh dấu mục đang đọc (IntersectionObserver),
  ẩn khi < 2 tiêu đề hoặc màn hình < 1280px.
- URL giữ trạng thái `?project=&doc=&v=` (+ `#tieu-de`): gửi link là người nhận mở đúng chỗ,
  nút Back của trình duyệt hoạt động.
- Màn hình hẹp (< 960px): thanh bên gập thành nút "Danh sách tài liệu"; dưới 1280px mục lục ẩn.
- In (Ctrl+P / nút In): chỉ in nội dung tài liệu, ẩn thanh bên, mục lục, nút.

### 5.2 Trang chủ của dự án (chưa chọn tài liệu)

4 chỉ số: số tài liệu, số cập nhật 30 ngày, độ đầy đủ %, số "có thể đã cũ"; danh sách **Cập nhật
gần đây**; với người có quyền sửa: thẻ **Còn thiếu — bắt đầu từ mẫu** (mỗi nhóm cốt lõi chưa có
tài liệu là một thẻ mẫu, bấm là mở trình soạn với mẫu đó). Dự án trống: trạng thái rỗng có hướng
dẫn và nút tải lên.

### 5.3 Thêm tài liệu (hộp thoại nhanh)

Hai tab:
1. **Tải file lên**: chọn nhóm áp cho cả lượt → kéo thả **nhiều file** (`.md .markdown .txt .doc
   .docx .xlsx .pdf`, ≤ 20 MB mỗi file). Kiểm loại và dung lượng ngay trên trình duyệt; tải tuần tự,
   mỗi file một dòng tiến độ (chờ / đang tải / xong + tiêu đề nhận được / lỗi + lý do). Mỗi file
   thành một tài liệu; tiêu đề lấy từ tiêu đề `#` đầu tiên trong file, không có thì từ tên file. Tải
   1 file xong thì mở luôn tài liệu đó.
2. **Soạn mới**: 8 thẻ mẫu (nhãn "còn thiếu" với nhóm chưa có) + "Trang trống".

### 5.4 Trình soạn (drawer rộng)

- Tiêu đề, nhóm, mô tả ngắn; thanh công cụ chèn cú pháp (H2, H3, đậm, nghiêng, code, danh sách,
  việc cần làm, link, khối code, bảng, khung Lưu ý / Cảnh báo); 3 chế độ **Soạn | Song song | Xem
  trước** (xem trước dùng đúng bộ hiển thị của trang đọc).
- Mẫu: đếm "Còn N chỗ `<…>` cần điền", bấm để nhảy tới chỗ tiếp theo.
- Ghi chú thay đổi (hiện trong lịch sử). Lưu bằng nút hoặc Ctrl/⌘+S.
- **Bản nháp** tự lưu trong trình duyệt (localStorage, chỉ là tiện ích) mỗi 0,8 giây; mở lại thấy
  "Có bản nháp chưa lưu" để khôi phục. Đóng khi còn thay đổi chưa lưu thì hỏi lại.
- **Chống ghi đè**: lưu gửi `base_version`; ai khác vừa lưu phiên bản mới → 409, nội dung đang soạn
  vẫn còn (và còn trong bản nháp). Lưu mà nội dung không đổi → không tạo phiên bản rỗng.

### 5.5 Lịch sử phiên bản (drawer)

Mỗi phiên bản: số, nhãn Hiện tại, nguồn (Tải file lên / Sửa trên web / Tạo từ mẫu / Khôi phục), ai,
khi nào, số ký tự, ghi chú, file gốc (tải về). **Xem thay đổi**: diff theo dòng với phiên bản trước
(LCS, gộp đoạn không đổi, đếm +/− dòng). **Xem bản này** mở bản cũ trên trang đọc với cảnh báo và
nút "Khôi phục bản này". Khôi phục tạo phiên bản mới.

### 5.6 Tìm kiếm

- Toàn văn PostgreSQL trên văn bản **bỏ dấu** (gõ "hoan tien" tìm được "hoàn tiền"), từ khoá như
  Stetho (`app/assistant/knowledge.terms`). Xếp hạng: số từ khoá khớp → điểm `ts_rank_cd` → từ khoá
  nằm trong tiêu đề tài liệu hoặc tiêu đề mục (+1 mỗi từ) → từ có dấu khớp đúng (+0,5). Mỗi tài
  liệu một kết quả (mục tốt nhất), kèm tiêu đề mục và đoạn trích ~220 ký tự có tô sáng.
- Mặc định trong dự án đang chọn; tick "Tìm ở mọi dự án" để tìm mọi dự án mình được xem. Tài liệu
  lưu trữ không xuất hiện.
- Bấm kết quả → mở tài liệu, cuộn tới đúng mục, tô sáng từ khoá; ô "Tìm trong tài liệu" đếm số lần
  xuất hiện.

## 6. Hiển thị sau khi tải lên **[Quyết định]**

### 6.1 Chuyển đổi (server, `app/assistant/importers.py`)

| Loại | Cách đọc | Giữ được | Mất |
|---|---|---|---|
| `.md`, `.markdown` | nguyên văn (UTF-8, thử cp1258/cp1252) | mọi thứ | — |
| `.txt` | nguyên văn; không có cú pháp Markdown nào thì đánh dấu `plain` và hiển thị giữ xuống dòng | nội dung | — |
| `.docx` | `word/document.xml` bằng thư viện chuẩn: tiêu đề theo style Heading 1–4, danh sách, bảng | cấu trúc, bảng | ảnh, màu, chú thích |
| `.doc` (Word 97-2003) | đọc OLE + piece table bằng thư viện chuẩn (`app/assistant/worddoc.py`): văn bản chính, bảng → bảng Markdown, bỏ mã field | chữ, bảng | tiêu đề (không đọc style), ảnh |
| `.xlsx` | mỗi sheet một mục `##` + bảng (≤ 2.000 dòng, 30 cột), giá trị đã tính, không chạy công thức | dữ liệu | định dạng ô |
| `.pdf` | lớp chữ từng trang (pypdf, ≤ 500 trang), mỗi trang có mốc `--- Trang N ---` | chữ, thứ tự trang | bố cục (xem bằng "Bản gốc PDF") |

An toàn: chặn zip bomb và XML có DTD (docx/xlsx), mọi offset của `.doc` được kiểm biên (file
dựng ác ý chỉ báo lỗi, không lặp vô hạn), PDF có mật khẩu hoặc chỉ là ảnh scan bị từ chối với lời
giải thích rõ. Không file nào được thực thi.

### 6.2 Hiển thị (web, `components/DocMarkdown.tsx`)

Dựng phần tử React, **không bao giờ chèn HTML thô** (file tải lên không thể chèn script):
- tiêu đề có neo `#` (dùng cho mục lục, chia sẻ link tới mục), cấp tiêu đề hạ một bậc dưới H1 của
  trang;
- đoạn văn, danh sách lồng nhau, danh sách việc `- [ ]`/`- [x]`, trích dẫn;
- khung **Lưu ý / Mẹo / Quan trọng / Cảnh báo / Thận trọng** (`> [!NOTE]` … `> [!CAUTION]`), màu
  theo token sáng/tối;
- khối code có nhãn ngôn ngữ và nút **Chép**;
- bảng thật (có căn trái/giữa/phải theo `:--`), cuộn ngang khi rộng;
- ảnh `![…](https://…)` (không gửi Referer), link http(s)/mailto/neo/đường dẫn nội bộ (link lạ bị
  bỏ, chỉ giữ chữ), link ngoài mở tab mới `noopener`;
- mốc trang PDF thành vạch "Trang N";
- tô sáng từ khoá tìm kiếm không phân biệt dấu.

PDF có hai chế độ: **Văn bản** (tìm được, có mục lục) và **Bản gốc PDF** (trình xem PDF của trình
duyệt; trang lấy file qua API rồi mở bằng `blob:` URL nên không cần nới `X-Frame-Options`). Các
loại khác: nút tải file gốc (luôn `attachment`, kèm `Content-Security-Policy: sandbox` và
`nosniff`).

## 7. Mẫu tài liệu (`app/docs/templates.py`) **[Quyết định]**

8 mẫu, mỗi mẫu có khung Lưu ý nói tài liệu dành cho ai và giữ thế nào cho đúng, bảng điền sẵn cột,
checklist, và chỗ `<…>`:

| Mẫu | Nhóm | Nội dung chính |
|---|---|---|
| Tổng quan dự án | overview | mục tiêu/phạm vi, chỉ số thành công, thành phần + repo, môi trường, đội ngũ, tài liệu liên quan, thuật ngữ |
| Kiến trúc hệ thống | architecture | bối cảnh và hệ thống ngoài, thành phần, luồng chính (idempotent/transaction/bất đồng bộ), dữ liệu + tenant + PII, phi chức năng, hạ tầng, rủi ro/nợ kỹ thuật |
| Cài đặt môi trường phát triển | setup | công cụ + phiên bản, quyền cần xin, cấu hình `.env`, chạy, test trước khi push, lỗi thường gặp |
| Tài liệu API | api | base URL/Swagger, xác thực, quy ước (thời gian, phân trang, idempotency, rate limit), mã lỗi, endpoint mẫu, webhook |
| Quy trình triển khai | deploy | pipeline, checklist trước/sau, lệnh, **rollback** trước, migration không đảo ngược, lịch cấm triển khai |
| Runbook vận hành và xử lý sự cố | runbook | mức SEV, liên hệ khẩn, công cụ quan sát, kịch bản dấu hiệu → kiểm tra → xử lý, sau sự cố (RCA) |
| Quy ước code và quy trình | conventions | nhánh/commit (khớp chuẩn Jira spec 27), MR checklist, quy ước code, bảo mật bắt buộc, Definition of Done; gợi ý đưa quy tắc bắt buộc vào Quy tắc review |
| Bản ghi quyết định kiến trúc (ADR) | decision | trạng thái, bối cảnh, phương án, quyết định, hệ quả |

## 8. Độ đầy đủ và độ mới

- **Độ đầy đủ** = số nhóm cốt lõi (7 nhóm, trừ `decision` và `other`) có ít nhất một tài liệu đang
  dùng / 7. Hiện ở thanh bên và trang chủ dự án; là tín hiệu cho quản lý, **không** vào điểm DPI.
- **Có thể đã cũ**: tài liệu không cập nhật hơn `STALE_DAYS = 180` ngày: chấm vàng ở cây tài liệu,
  số đếm ở trang chủ, thông báo nhẹ trên trang đọc.

## 9. API (`app/api/routers/project_docs.py`)

| Method | Path | Quyền | Ghi chú |
|---|---|---|---|
| GET | `/docs/projects` | docs.view | dự án xem được + số tài liệu + `can_edit` |
| GET | `/docs/meta` | docs.view | loại file nhận, dung lượng tối đa, nhóm, mẫu |
| GET | `/docs?business_project_id&archived` | docs.view | danh sách + nhóm + `coverage` |
| GET | `/docs/search?q&business_project_id&limit` | docs.view | kết quả theo mục |
| GET | `/docs/{id}` | docs.view | chi tiết + nội dung phiên bản hiện tại |
| GET | `/docs/{id}/versions`, `/docs/{id}/versions/{n}` | docs.view | lịch sử, một phiên bản |
| GET | `/docs/{id}/versions/{n}/original?inline` | docs.view | file gốc (`inline` chỉ với PDF) |
| POST | `/docs` | docs.edit | tạo từ trình soạn / mẫu |
| POST | `/docs/upload?business_project_id&filename&category&title` | docs.edit | thân là byte file |
| POST | `/docs/{id}/upload?filename&note` | docs.edit | phiên bản mới từ file |
| PUT | `/docs/{id}/content` | docs.edit | phiên bản mới từ trình soạn, `base_version` |
| POST | `/docs/{id}/versions/{n}/restore` | docs.edit | khôi phục thành phiên bản mới |
| PATCH | `/docs/{id}` | docs.edit | tiêu đề, nhóm, mô tả, ghim, vị trí, lưu trữ |
| DELETE | `/docs/{id}` | docs.edit | chỉ tài liệu đã lưu trữ |

Giới hạn: file ≤ 20 MB (đọc theo luồng, dừng ngay khi vượt), nội dung ≤ 400.000 ký tự, tiêu đề
≤ 200, ghi chú ≤ 300.

## 10. Ngoài phạm vi (IDEAS.md)

- Ảnh trong Word/PDF (trích ảnh, lưu và hiển thị), sơ đồ Mermaid, tô màu cú pháp code.
- Stetho trả lời từ tài liệu dự án (cần lọc theo quyền xem dự án của người hỏi).
- Bình luận / góp ý trên tài liệu, nhắc người phụ trách cập nhật tài liệu cũ, chủ sở hữu tài liệu.
- Đồng bộ từ Confluence / Google Drive / thư mục `docs/` của repo (link đơn lẻ: §12).
- OCR cho PDF scan.

## 11. Kiểm thử

- `tests/api/test_project_docs.py`: tải `.md`, `.pdf` (tự dựng), `.doc` (file Word thật), `.txt`;
  thành viên đọc được không sửa được; người ngoài dự án nhận 404; tìm không dấu ra đúng mục;
  file gốc inline/attachment + CSP; phiên bản, 409 khi sửa trên bản cũ, lưu không đổi không tạo
  phiên bản, khôi phục, đổi tên thì tìm theo tên mới, lưu trữ thì không tìm thấy, xoá cần lưu trữ
  trước; audit; mẫu; từ chối file sai loại, PDF hỏng, file rỗng.
- `tests/permissions/test_catalog.py`: bộ quyền mặc định khớp bản sao trong migration.

## 12. Tổng hợp bằng AI từ link và file — user 2026-11-12 **[Quyết định]**

Yêu cầu: nhập một link, LLM đọc và tổng hợp thành tài liệu cụ thể; có thể bổ sung vào tài liệu
đang có hoặc nâng cấp nó. Tài liệu có chuẩn riêng, được bổ sung dần từ nhiều nguồn. Bổ sung cùng
ngày: nguồn còn có thể là **file tải lên** (§12.8).

### 12.1 Quyết định

| Câu hỏi | Chọn | Vì sao |
|---|---|---|
| Kết quả AI ghi thẳng vào tài liệu? | **Không: bản nháp, người duyệt rồi mới lưu** | AI có thể bịa hoặc làm rơi nội dung; tài liệu là nguồn tin cậy của dự án. Bản nháp xem trước, so sánh với bản hiện tại, sửa Markdown, rồi “Lưu thành phiên bản” (nguồn `link`) |
| "Chuẩn riêng" là gì? | **Mẫu của loại tài liệu** (§7) | Đã có 8 mẫu theo nhóm; AI viết đúng bố cục và tên mục của mẫu. Nhóm "Khác" dùng một khung chung |
| Các chế độ | **Tạo mới / Bổ sung / Nâng cấp theo chuẩn** | *Bổ sung*: giữ nguyên bố cục và câu chữ, chỉ thêm, không xoá; mâu thuẫn thì giữ câu cũ và thêm khung `> [!WARNING]`. *Nâng cấp*: sắp lại theo mẫu chuẩn, giữ mọi sự thật cũ, gộp nguồn mới |
| Nhiều nguồn | **≤ 5 link mỗi lần; mỗi phiên bản ghi lại link của nó** | Tài liệu hiện mục “Nguồn đã tổng hợp” gộp link của mọi phiên bản; trong bài AI ghi `[n]` sau câu lấy từ nguồn n và mục “Nguồn tham khảo” cuối bài |
| Link GitLab nội bộ | **Đọc qua kết nối GitLab nếu repo thuộc chính dự án đó** | README/file/wiki thường nằm ở repo riêng tư. Chỉ repo đang theo dõi, gắn với dự án của tài liệu (token kết nối thấy nhiều hơn người dùng); repo tắt “cho gửi ra LLM ngoài” bị từ chối |
| Link khác | **Như Stetho** (`importers.from_url`) | Đã chặn SSRF, giới hạn 2 MB/30 giây; máy nội bộ chỉ khi có trong `ASSISTANT_URL_HOSTS`. Trang cần đăng nhập không đọc được → hướng dẫn tải file lên |
| Chạy ở đâu | **Job nền** trên hàng `reviews_asked` | Gọi LLM mất 1–5 phút; giao diện hiện tiến độ (Xếp hàng → Đọc link → AI đang viết → Sẵn sàng duyệt), đóng cửa sổ vẫn chạy |
| Chi phí | Kiểm ngân sách LLM tháng trước khi xếp hàng và trước khi gọi; ghi `doc.synthesize.cost` vào audit, cộng vào `month_spend` (`doc_drafts_usd`) | Một ngân sách chung (19 §6) |
| Giới hạn | **20 lần/người/24 giờ** (tính cả “Viết lại”), **3 bản nháp đang viết** một lúc | Tránh đốt ngân sách; khoá dòng `users` khi đếm để hai tab không vượt |
| Ghi đè | Bản nháp nhớ `base_version`; lưu khi tài liệu đã có phiên bản mới hơn → **409**, nút “Viết lại” viết trên bản mới nhất | Không lặng lẽ xoá thay đổi của người khác |
| An toàn prompt | Nội dung trang là dữ liệu: bọc trong `<source>`, ký tự `<`/`>`/`"` của trang được thay bằng ký tự trông giống để không đóng được thẻ; system prompt dặn bỏ qua mọi chỉ dẫn trong nguồn | Đầu ra là Markdown, hiển thị bằng trình dựng không HTML (§6.2) |

### 12.2 Dữ liệu (migration `e3a5b7c9d1f4`)

`project_document_drafts`: dự án, `document_id` (tài liệu bổ sung/nâng cấp; với "new" là tài liệu
tạo ra khi lưu), `mode`, `category`, `urls`, `instruction`, `status`
(`queued → fetching → writing → ready | failed`, rồi `applied | discarded`), `error`,
`base_version`, `title`, `summary`, `body`, `result` (`changes`, `gaps`, `conflicts`, `warnings`,
`placeholders`), `sources` (mỗi link: `url`, `title`, `via` web/gitlab, `chars`, `error`), `model`,
`cost_usd`, `applied_version`, `requested_at` (mã lượt chạy: lượt cũ bị coi là chết sau 20 phút
không ghi đè được lượt mới). Phiên bản có thêm nguồn `link`, `meta.links` (URL bỏ query string).

### 12.3 Kiểm tra đầu ra

- Thân rỗng, quá 400.000 ký tự, không phải JSON → lỗi, cho “Thử lại”.
- Bọc trong ```` ```markdown ```` thì gỡ ra.
- Bổ sung/nâng cấp mà ngắn hơn bản hiện tại > 15% → cảnh báo “có thể mất nội dung”.
- Đếm chỗ `<cần bổ sung: …>` (nguồn không có thông tin mà chuẩn yêu cầu) → nhãn trên bản nháp.
- Link không đọc được: ghi lý do từng link; còn ít nhất một link đọc được thì vẫn viết và cảnh
  báo; không link nào đọc được thì dừng, không gọi LLM.
- Tài liệu hiện tại > 120.000 ký tự không bổ sung/nâng cấp được (tách nhỏ trước).

### 12.4 UI

- Trang chủ dự án: thẻ **Tổng hợp từ link** (người sửa được) liệt kê bản nháp và trạng thái
  (Đang chờ / Đọc link / AI đang viết / Chờ duyệt / Lỗi / Đã lưu), tự cập nhật khi có bản đang viết.
- Đầu trang: nút **Tổng hợp từ link** cạnh “Thêm tài liệu”; hộp “Thêm tài liệu” có tab thứ ba
  **Tổng hợp từ link (AI)**.
- Trong tài liệu: nút **Bổ sung từ link** (chọn Bổ sung hoặc Nâng cấp); khung báo khi tài liệu có
  bản nháp đang viết / chờ duyệt; cuối bài mục **Nguồn đã tổng hợp**.
- Form: ô link (mỗi dòng một link, kiểm ngay http/https và số lượng), 3 thẻ chế độ có giải thích,
  nhóm tài liệu (chọn mẫu chuẩn) hoặc tài liệu cần bổ sung, yêu cầu thêm, số lần còn lại hôm nay.
- Duyệt (hộp thoại rộng, nút hành động luôn ở đáy): cảnh báo; tiêu đề, nhóm, mô tả; tab **Xem
  trước / So sánh với bản hiện tại / Sửa Markdown**; cột phải: AI đã làm, mâu thuẫn cần quyết,
  nguồn chưa có, danh sách nguồn (link lỗi gạch ngang kèm lý do), chi phí. Nút: Bỏ bản nháp, Viết
  lại, Tạo tài liệu / Lưu thành phiên bản N.

### 12.5 API (`app/api/routers/doc_drafts.py`, đăng ký trước `project_docs`)

| Phương thức | Đường dẫn | Ghi chú |
|---|---|---|
| GET | `/docs/drafts?business_project_id` | bản nháp của dự án (người sửa được), số lần đã dùng |
| POST | `/docs/drafts` | `mode`, `urls`, `document_id` / `category`, `instruction` → 201, xếp hàng |
| GET | `/docs/drafts/{id}` | chi tiết + nội dung |
| POST | `/docs/drafts/{id}/retry` | viết lại từ cùng link (tính vào giới hạn) |
| POST | `/docs/drafts/{id}/apply` | `body`/`title`/`summary`/`category`/`note` tuỳ chọn → phiên bản mới; 409 nếu tài liệu đã đổi |
| DELETE | `/docs/drafts/{id}` | bỏ (giữ dòng, xoá nội dung) |

### 12.6 Kiểm thử

`tests/api/test_doc_drafts.py`: tạo mới từ link → chạy job (LLM giả) → duyệt → lưu, nguồn hiện
trên tài liệu; bổ sung + cảnh báo ngắn hơn + 409 khi có phiên bản mới + viết lại rồi lưu; link
hỏng / hết ngân sách / đầu ra rỗng; bỏ bản nháp; người chỉ xem bị 403; link sai, có mật khẩu,
quá 5 link; giới hạn ngày; link GitLab của repo trong dự án (file, README, wiki), repo ngoài dự
án, repo không theo dõi, repo cấm gửi LLM; nội dung trang không đóng được thẻ nguồn; chi phí vào
`month_spend`.

### 12.7 Ngoài phạm vi (IDEAS.md)

- Tự động theo dõi link nguồn và đề xuất cập nhật khi trang thay đổi.
- Đọc Confluence / Google Docs bằng tài khoản kết nối (OAuth).
- Tổng hợp từ cả thư mục `docs/` của repo.

### 12.8 File tải lên làm nguồn (user 2026-11-12) **[Quyết định]**

- Cùng form, dưới ô link: vùng kéo thả nhiều file (.md, .txt, .doc, .docx, .xlsx, .pdf, ≤ 20 MB),
  bộ chuyển đổi của §6.1. Link + file chung giới hạn **5 nguồn** mỗi lần.
- Mỗi file được đọc ngay khi thả (`POST /docs/drafts/extract`, cần `docs.edit` trên dự án): trả về
  chữ (cắt ở 60.000 ký tự, báo nếu bị cắt), số ký tự, số trang; form hiện từng file và cho bỏ.
  Không lưu gì ở bước này.
- Khi gửi yêu cầu, chữ của các file đi kèm (`files`), bản nháp giữ **chỉ phần chữ** trong cột
  `project_document_drafts.files` (migration `f4b6c8d0e2a5`) để “Viết lại” được; file gốc không
  lưu; bỏ bản nháp thì xoá luôn phần chữ đó. Vì sao không lưu file gốc: bản nháp chỉ cần chữ, và
  muốn giữ file gốc thì đã có “Tải file lên” (§5.3).
- Trong prompt, nguồn là file mang `file="tên"` thay cho `url`; mục “Nguồn tham khảo” ghi tên file.
- Phiên bản ghi `meta.files`; mục “Nguồn đã tổng hợp” của tài liệu liệt kê cả file (không có link).
- Tên nút đổi thành **Tổng hợp bằng AI** / **Bổ sung bằng AI** (nguồn không chỉ là link).

### 12.9 Dán văn bản làm nguồn (user 2026-11-12) **[Quyết định]**

- Nút **Dán văn bản làm nguồn** dưới vùng kéo thả: ô tên (mặc định “Văn bản dán n”) + ô nội dung;
  “Thêm làm nguồn” đưa nó vào cùng danh sách nguồn với file. Dùng cho ghi chú họp, trao đổi trên
  chat, nội dung trang cần đăng nhập mà máy chủ không đọc được.
- Gửi kèm như một file (`files[]`, `kind = "paste"`), không cần bước đọc file; cắt ở 60.000 ký
  tự như file; tên các nguồn không được trùng (422). Chung giới hạn 5 nguồn với link và file.
- Nguồn hiện là “Văn bản dán” trong bản nháp, ghi chú phiên bản (“… từ 1 link và 1 văn bản dán”)
  và “Nguồn đã tổng hợp”.

### 12.10 Ảnh chụp màn hình làm nguồn (user 2026-11-12) **[Quyết định]**

| Câu hỏi | Chọn | Vì sao |
|---|---|---|
| Đọc ảnh bằng gì? | **Claude xem ảnh trực tiếp** (chế độ có công cụ của CLI, chỉ `Read`, trong một thư mục tạm chỉ chứa các ảnh) | Không thêm OCR; Claude hiểu cả bảng, sơ đồ, bố cục màn hình. Chế độ này đã dùng cho review sâu (spec 18): không công cụ nào khác, không đọc được gì ngoài thư mục |
| Đưa ảnh vào thế nào? | **Ctrl+V trong form**, hoặc kéo thả / chọn file ảnh cùng vùng với file tài liệu | Ảnh chụp màn hình thường nằm trong clipboard; ảnh dán tên `image.png` được đặt tên “Ảnh chụp hhmmss-n” |
| Lưu ở đâu, bao lâu? | Bảng `project_document_draft_images` (migration `a5c7e9f1b3d6`): tải lên ngay khi dán (chưa gắn bản nháp), gắn khi gửi yêu cầu; **xoá khi bản nháp được lưu hoặc bị bỏ**; ảnh không gửi bị xoá sau 1 ngày (dọn khi người đó dán ảnh tiếp) | Cần giữ để “Viết lại”; phiên bản chỉ ghi tên ảnh |
| Giới hạn | PNG, JPEG, WebP, GIF, **≤ 5 MB**, loại ảnh kiểm từ byte đầu (không tin tên / header); ≤ 20 ảnh chưa gửi mỗi người; chung giới hạn 5 nguồn | |
| Ai xem được ảnh? | Ảnh chưa gửi: chỉ người dán. Ảnh đã gắn bản nháp: người sửa được tài liệu của dự án (như bản nháp). Trả về kèm `nosniff` + CSP `sandbox` | |

- Prompt liệt kê `<image n file name>` sau các nguồn chữ và dặn đọc từng file bằng `Read`, chữ
  trong ảnh cũng là dữ liệu; ghi nguồn `[n] ảnh: tên`.
- Bản nháp hiện ảnh thu nhỏ trong danh sách nguồn (bấm mở ảnh gốc); ghi chú phiên bản “… từ 1
  ảnh”; “Nguồn đã tổng hợp” ghi “Ảnh chụp”.
- API: `POST /docs/drafts/images?business_project_id&filename` (thân là byte ảnh),
  `GET /docs/drafts/images/{id}`, `DELETE /docs/drafts/images/{id}` (ảnh chưa gửi);
  `POST /docs/drafts` nhận `image_ids`.

## 13. Ảnh trong tài liệu — user 2026-11-12 **[Quyết định]**

Yêu cầu: dán ảnh trực tiếp vào trình soạn tài liệu.

| Câu hỏi | Chọn | Vì sao |
|---|---|---|
| Đưa ảnh vào thế nào? | **Ctrl+V** trong ô soạn, **kéo thả** ảnh vào ô soạn, hoặc nút **Ảnh** trên thanh công cụ (chọn nhiều ảnh) | Ảnh chụp màn hình thường nằm trong clipboard |
| Ghi trong Markdown ra sao? | `![tên](asset:<id>)`, chèn tại con trỏ thành một khối riêng; trong lúc tải hiện chỗ giữ `![Đang tải ảnh…]()` rồi thay | Tài liệu không gắn với địa chỉ máy chủ; trình hiển thị đổi `asset:` thành `/api/v1/docs/assets/<id>` |
| Lưu ở đâu, bao lâu? | Bảng `project_document_assets` (migration `b6d8f0a2c4e7`), thuộc **dự án** (tài liệu mới chưa có id lúc dán), **không xoá theo phiên bản** (bản cũ vẫn hiện ảnh); cùng ảnh dán lại trong dự án = cùng một ảnh (sha256) | Lịch sử phiên bản phải xem lại được nguyên vẹn |
| Ai xem? | Ai đọc được tài liệu của dự án (`docs.view` + dự án trong tầm nhìn); thêm ảnh cần `docs.edit`. Trả về kèm `nosniff`, CSP `sandbox`, cache riêng tư 1 ngày | |
| Giới hạn | PNG, JPEG, WebP, GIF, ≤ 5 MB, loại kiểm từ byte đầu | |
| Ảnh từ trang khác (`![](https://…)`) | **Không tự tải**: hiện thành link “🖼 tên” để bấm mở | Ảnh ngoài cho trang đó biết ai đang đọc tài liệu, và một địa chỉ dựng sẵn có thể mang nội dung ra ngoài (rủi ro đã nêu ở §12). Chỉ ảnh `asset:` và đường dẫn cùng trang được tải |
| AI bổ sung / nâng cấp | Giữ nguyên ảnh `asset:` của tài liệu (prompt dặn; bộ lọc đầu ra §12.3 giữ ảnh `asset:`, vẫn gỡ ảnh ngoài) | |

### 13.1 Cỡ ảnh (user 2026-11-12) **[Quyết định]**

- Cỡ ghi ngay sau tên ảnh, theo quy ước quen thuộc (Obsidian): `![Sơ đồ|50%](asset:12)` = 50% bề
  rộng trang, `![Sơ đồ|480](asset:12)` = 480 px (không bao giờ rộng hơn trang); không ghi = cỡ
  gốc. Nhận 5–100% và 16–2000 px; giá trị khác bị bỏ qua. Trình Markdown khác chỉ hiện “Sơ
  đồ|50%” làm chữ thay thế, ảnh không hỏng. Vì sao không dùng HTML `<img width>`: tài liệu hiển
  thị không HTML (§6.2).
- Trình soạn: khi con trỏ nằm trên dòng ảnh, hoặc bấm vào ảnh trong khung xem trước (con trỏ nhảy
  tới dòng đó), hiện thanh **Cỡ ảnh**: Nhỏ (25%), Vừa (50%), Lớn (75%), Rộng hết (100%), Cỡ gốc,
  hoặc nhập số px; bấm là viết lại đúng dòng ảnh đó.
- AI bổ sung / nâng cấp giữ nguyên cả dòng ảnh (tên, cỡ, `asset:`).

### 13.2 Kéo góc ảnh để đổi cỡ (user 2026-11-12) **[Quyết định]**

- Trong khung xem trước của trình soạn (chỉ ở đó; trang đọc tài liệu không có), di chuột lên ảnh
  hiện viền và một **tay nắm ở góc dưới phải**. Kéo: ảnh co giãn trực tiếp, nhãn hiện % bề rộng.
  Thả: cỡ làm tròn bước 5% (5–100%) và ghi vào dòng ảnh (`![tên|45%](asset:…)`), thanh Cỡ ảnh
  (§13.1) hiện cho dòng đó.
- Vì sao ghi theo %: tài liệu đọc trên nhiều cỡ màn hình; px chỉ khi gõ số ở thanh Cỡ ảnh.
- Ảnh căn giữa nở về hai phía: bề rộng tăng gấp đôi quãng kéo để tay nắm đi theo con trỏ.
- Bàn phím: tay nắm nhận focus (Tab), phím mũi tên trái/phải (lên/xuống) đổi ±5%.

### 13.3 Căn ảnh trái / giữa / phải (user 2026-11-12) **[Quyết định]**

- Ghi sau tên (và cỡ, nếu có): `![Sơ đồ|50%|right](asset:12)`, `|left`, `|right`; giữa là mặc
  định nên không ghi `|center` (đọc được nếu có). Thứ tự cỡ / căn không quan trọng khi đọc; khi
  viết luôn là tên → cỡ → căn.
- Thanh ảnh trong trình soạn (§13.1) có thêm nhóm **Trái / Giữa / Phải**; chú thích ảnh căn theo.
- Chỉ căn, **không cho chữ chảy quanh ảnh**: tài liệu đọc cả trên điện thoại, chữ bao quanh ảnh
  hẹp sẽ vỡ dòng; ảnh căn trái/phải vẫn chiếm một khối riêng.
- Kéo góc (§13.2): ảnh căn phải có tay nắm ở góc dưới **trái** và nở sang trái.

Ngoài phạm vi (IDEAS.md): dọn ảnh không còn tài liệu nào dùng; ảnh nhúng sẵn trong file Word/PDF
tải lên; chữ chảy quanh ảnh.

