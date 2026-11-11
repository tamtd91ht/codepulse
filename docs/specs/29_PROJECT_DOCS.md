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
- Đồng bộ từ Confluence / Google Drive / thư mục `docs/` của repo.
- OCR cho PDF scan.

## 11. Kiểm thử

- `tests/api/test_project_docs.py`: tải `.md`, `.pdf` (tự dựng), `.doc` (file Word thật), `.txt`;
  thành viên đọc được không sửa được; người ngoài dự án nhận 404; tìm không dấu ra đúng mục;
  file gốc inline/attachment + CSP; phiên bản, 409 khi sửa trên bản cũ, lưu không đổi không tạo
  phiên bản, khôi phục, đổi tên thì tìm theo tên mới, lưu trữ thì không tìm thấy, xoá cần lưu trữ
  trước; audit; mẫu; từ chối file sai loại, PDF hỏng, file rỗng.
- `tests/permissions/test_catalog.py`: bộ quyền mặc định khớp bản sao trong migration.
