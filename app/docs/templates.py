"""Mẫu tài liệu dự án (docs/specs/29 §6): a starting skeleton for each kind of document a dự án
should have, so a team writes what readers need instead of a blank page. Each template is
Markdown with `<…>` placeholders and callouts that explain what belongs where; the author
replaces them (the editor shows how many placeholders are left)."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Template:
    key: str
    title: str
    category: str
    summary: str
    body: str


OVERVIEW = """# Tổng quan dự án <Tên dự án>

> [!NOTE]
> Tài liệu đầu tiên người mới đọc. Trả lời được: dự án làm gì, cho ai, gồm những phần nào, ai
> phụ trách, tìm thêm thông tin ở đâu. Giữ dưới 2 trang; chi tiết để ở các tài liệu khác.

## Mục tiêu và phạm vi

- **Bài toán**: <vấn đề nghiệp vụ dự án giải quyết, 1–2 câu>
- **Người dùng**: <nhóm người dùng chính, ví dụ: tổng đài viên, khách hàng doanh nghiệp, admin nội bộ>
- **Trong phạm vi**: <các tính năng chính>
- **Ngoài phạm vi**: <những gì dự án cố ý không làm>

## Chỉ số thành công

| Chỉ số | Mục tiêu | Cách đo |
|---|---|---|
| <ví dụ: thời gian phản hồi API p95> | <≤ 300 ms> | <Grafana dashboard …> |
| <ví dụ: tỉ lệ lỗi> | <≤ 0,1%> | <…> |

## Thành phần chính

| Thành phần | Repo | Công nghệ | Mô tả ngắn |
|---|---|---|---|
| <Backend API> | <group/backend> | <Java 21, Spring Boot> | <…> |
| <Web> | <group/web> | <Next.js> | <…> |
| <Worker> | <group/worker> | <…> | <…> |

## Môi trường

| Môi trường | URL | Nhánh triển khai | Ghi chú |
|---|---|---|---|
| Dev | <https://dev…> | `develop` | <…> |
| Staging | <https://staging…> | `release/*` | <…> |
| Production | <https://…> | `main` | <…> |

## Đội ngũ và liên hệ

| Vai trò | Người phụ trách | Kênh liên hệ |
|---|---|---|
| Product owner | <tên> | <…> |
| Tech lead | <tên> | <…> |
| On-call | <lịch trực / nhóm> | <…> |

## Tài liệu liên quan

- Kiến trúc hệ thống: <link tài liệu Kiến trúc>
- Cài đặt môi trường dev: <link>
- Tài liệu API: <link>
- Quy trình triển khai và runbook: <link>
- Bảng công việc (Jira): <link>

## Thuật ngữ

| Thuật ngữ | Nghĩa |
|---|---|
| <tenant> | <một khách hàng doanh nghiệp, dữ liệu tách biệt> |
"""

ARCHITECTURE = """# Kiến trúc hệ thống

> [!NOTE]
> Mô tả hệ thống **đang chạy thật**, không phải bản thiết kế mong muốn. Cập nhật khi thêm thành
> phần, đổi luồng dữ liệu hoặc đổi hạ tầng. Mỗi quyết định lớn ghi thêm một ADR.

## Bối cảnh (context)

<Hệ thống nằm ở đâu trong bức tranh chung: ai/hệ thống nào gọi vào, hệ thống gọi ra những đâu.>

| Hệ thống ngoài | Chiều | Giao thức | Mục đích |
|---|---|---|---|
| <Cổng thanh toán> | ra | HTTPS/REST | <…> |
| <Hệ thống CRM> | vào | Webhook | <…> |

## Thành phần (container)

```text
<Vẽ sơ đồ dạng chữ, ví dụ:>
[Web] --HTTPS--> [API Gateway] --> [Service A] --> [PostgreSQL]
                                \\-> [Service B] --> [Redis]
                                      \\-> [Kafka] --> [Worker]
```

| Thành phần | Trách nhiệm | Dữ liệu sở hữu | Mở rộng thế nào |
|---|---|---|---|
| <Service A> | <…> | <bảng/collection …> | <chạy N bản, stateless> |

## Luồng chính

### <Tên luồng, ví dụ: Đặt hàng>

1. <Bước 1: ai gọi gì>
2. <Bước 2>
3. <Bước 3: dữ liệu được ghi ở đâu, sự kiện nào được phát>

> [!IMPORTANT]
> Ghi rõ chỗ nào cần **idempotent**, chỗ nào có **transaction**, chỗ nào **bất đồng bộ** và
> cách thử lại khi lỗi.

## Dữ liệu

- **Cơ sở dữ liệu**: <PostgreSQL 16 / MongoDB …>, sơ đồ bảng chính: <link hoặc mô tả>
- **Phân tách tenant**: <cách cô lập dữ liệu giữa khách hàng>
- **Dữ liệu nhạy cảm**: <trường nào là PII, mã hoá thế nào, ai được đọc>
- **Lưu trữ và xoá**: <giữ bao lâu, job xoá>

## Yêu cầu phi chức năng

| Thuộc tính | Mục tiêu | Cách đảm bảo |
|---|---|---|
| Hiệu năng | <p95 ≤ … ms> | <cache, index …> |
| Sẵn sàng | <99,9%> | <nhiều bản, health check> |
| Bảo mật | <…> | <xác thực, phân quyền, audit> |
| Quan sát | <log/metric/trace> | <ELK, Prometheus, …> |

## Hạ tầng và triển khai

<Kubernetes/VM, số node, cấu hình chính, nơi lưu secret, CI/CD.>

## Rủi ro và nợ kỹ thuật đã biết

- [ ] <Rủi ro 1 và kế hoạch xử lý>
- [ ] <Nợ kỹ thuật 1>
"""

SETUP = """# Cài đặt môi trường phát triển

> [!TIP]
> Mục tiêu: người mới chạy được dự án trên máy mình **trong dưới 1 giờ**. Mỗi khi ai đó gặp
> bước khó, sửa tài liệu này ngay.

## Yêu cầu

| Công cụ | Phiên bản | Kiểm tra |
|---|---|---|
| <Git> | <≥ 2.40> | `git --version` |
| <Node.js> | <20 LTS> | `node -v` |
| <Docker Desktop> | <≥ 4.30> | `docker info` |
| <JDK / Python …> | <…> | <…> |

Quyền cần xin trước: <GitLab group …, VPN, tài khoản DB dev, secret vault …>

## Lấy mã nguồn

```bash
git clone <git@gitlab…/group/repo.git>
cd <repo>
```

## Cấu hình

1. Chép file mẫu: `cp .env.example .env`
2. Điền các biến:

| Biến | Ý nghĩa | Lấy ở đâu |
|---|---|---|
| `<DATABASE_URL>` | <chuỗi kết nối DB dev> | <vault/dev/…> |
| `<API_KEY>` | <…> | <…> |

> [!CAUTION]
> Không commit file `.env` hay bất kỳ secret nào. Repo đã có `.gitignore` cho file này.

## Chạy

```bash
<docker compose up -d   # DB, Redis…>
<npm install>
<npm run dev>
```

Mở <http://localhost:3000>, đăng nhập bằng <tài khoản test …>.

## Chạy test và kiểm tra trước khi đẩy code

```bash
<npm run lint>
<npm test>
```

## Lỗi thường gặp

| Triệu chứng | Nguyên nhân | Cách xử lý |
|---|---|---|
| <Port 5432 đã dùng> | <Postgres cài sẵn trên máy> | <tắt service hoặc đổi port> |
| <…> | <…> | <…> |
"""

API = """# Tài liệu API

> [!NOTE]
> Nếu đã có OpenAPI/Swagger, ghi link ở đây và chỉ viết những gì Swagger không nói được:
> xác thực, quy ước chung, lỗi, giới hạn và ví dụ luồng hoàn chỉnh.

- **Base URL**: `<https://api…/v1>`
- **Swagger / OpenAPI**: <link>
- **Phiên bản hiện tại**: <v1>, chính sách đổi phiên bản: <…>

## Xác thực

<Bearer JWT / API key / OAuth2: cách lấy token, thời hạn, cách làm mới.>

```http
GET /v1/<resource>
Authorization: Bearer <token>
```

## Quy ước chung

- Định dạng: JSON UTF-8, thời gian ISO 8601 có múi giờ (`2026-11-11T09:00:00+07:00`)
- Phân trang: `?page=1&page_size=20`, trả về `{ items, total, page }`
- Idempotency: <header `Idempotency-Key` cho các lệnh tạo/thanh toán>
- Giới hạn tần suất: <… request/phút>, vượt thì `429` kèm `Retry-After`

## Mã lỗi

| HTTP | Mã | Ý nghĩa | Client nên làm gì |
|---|---|---|---|
| 400 | `invalid_argument` | <tham số sai> | <sửa request> |
| 401 | `unauthorized` | <token hết hạn> | <làm mới token> |
| 403 | `forbidden` | <không có quyền> | <…> |
| 409 | `conflict` | <trùng/đã xử lý> | <…> |
| 5xx | `internal` | <lỗi hệ thống> | <thử lại có backoff> |

## Endpoint

### <POST /v1/orders> — <Tạo đơn hàng>

**Request**

```json
{
  "<field>": "<value>"
}
```

**Response 201**

```json
{
  "id": "<…>",
  "status": "<created>"
}
```

| Trường | Kiểu | Bắt buộc | Mô tả |
|---|---|---|---|
| `<field>` | string | có | <…> |

## Webhook / sự kiện phát ra

| Sự kiện | Khi nào | Payload |
|---|---|---|
| `<order.created>` | <…> | <link schema> |
"""

DEPLOY = """# Quy trình triển khai

> [!IMPORTANT]
> Ai cũng phải triển khai được bằng tài liệu này mà không cần hỏi người khác. Ghi rõ cách
> **quay lui (rollback)** trước khi ghi cách triển khai.

## Tổng quan pipeline

| Bước | Công cụ | Điều kiện qua |
|---|---|---|
| Build + test | <GitLab CI> | <lint, unit test, coverage ≥ …> |
| Review | <MR + CodePulse> | <không còn critical/high mở> |
| Staging | <ArgoCD / script …> | <smoke test pass> |
| Production | <…> | <được <vai trò> duyệt> |

## Trước khi triển khai

- [ ] MR đã merge, pipeline xanh
- [ ] Migration DB tương thích ngược (code cũ chạy được trên schema mới)
- [ ] Biến cấu hình mới đã có ở môi trường đích
- [ ] Đã báo <kênh> thời điểm triển khai
- [ ] Đã biết cách rollback cho thay đổi này

## Triển khai

```bash
<lệnh hoặc thao tác, từng bước>
```

## Kiểm tra sau triển khai

- [ ] Health check `<GET /health>` trả 200
- [ ] Dashboard lỗi không tăng trong 15 phút
- [ ] <Luồng nghiệp vụ chính> chạy được

## Rollback

```bash
<lệnh quay về phiên bản trước>
```

> [!WARNING]
> Migration không đảo ngược được: <cách xử lý, ví dụ khôi phục từ backup, viết migration bù>.

## Lịch và quy định

- Cửa sổ triển khai production: <thứ … giờ …>
- Không triển khai: <thứ 6 sau 15h, ngày lễ, …>
- Hotfix khẩn: <ai duyệt, quy trình rút gọn>
"""

RUNBOOK = """# Runbook vận hành và xử lý sự cố

> [!CAUTION]
> Dùng lúc có sự cố: viết ngắn, theo bước, lệnh chép chạy được ngay. Mỗi sự cố mới xử lý xong
> thì bổ sung một mục vào đây.

## Mức độ sự cố

| Mức | Định nghĩa | Thời gian phản hồi | Báo cho |
|---|---|---|---|
| SEV1 | <toàn hệ thống ngừng / mất dữ liệu> | <15 phút> | <…> |
| SEV2 | <tính năng chính lỗi với nhiều khách> | <30 phút> | <…> |
| SEV3 | <lỗi cục bộ, có cách vòng> | <1 ngày làm việc> | <…> |

## Liên hệ khẩn

| Vai trò | Người | Số điện thoại / kênh |
|---|---|---|
| On-call chính | <…> | <…> |
| DBA / hạ tầng | <…> | <…> |

## Công cụ quan sát

- Log: <link Kibana/Loki, truy vấn mẫu>
- Metric: <link Grafana>
- Trace: <link>
- Cảnh báo: <kênh nhận alert>

## Kịch bản xử lý

### <Tên sự cố, ví dụ: API trả 5xx tăng đột biến>

**Dấu hiệu**: <alert nào, triệu chứng gì>

**Kiểm tra nhanh**

```bash
<kubectl get pods -n …>
<kubectl logs deploy/… --since=10m | grep ERROR>
```

**Xử lý**

1. <Bước 1>
2. <Bước 2>
3. Không đỡ được sau <15 phút>: <rollback theo tài liệu Triển khai / gọi …>

### <Tên sự cố khác: DB đầy kết nối>

**Dấu hiệu**: <…>

**Xử lý**: <…>

## Sau sự cố

- [ ] Ghi timeline và nguyên nhân gốc (RCA) trong <nơi lưu>
- [ ] Tạo task phòng ngừa, gắn nhãn `incident`
- [ ] Cập nhật runbook này
"""

CONVENTIONS = """# Quy ước code và quy trình làm việc

> [!TIP]
> Quy ước nào máy kiểm được (lint, format, test) thì để máy kiểm; tài liệu này giữ những gì
> cần người hiểu. Quy tắc review bắt buộc nên đưa thêm vào **Quy tắc review** của dự án trên
> CodePulse để AI review đối chiếu.

## Nhánh và commit

- Nhánh: `feature/<JIRA-123>-mo-ta-ngan`, `fix/<JIRA-456>-…`, `hotfix/…`
- Commit theo Conventional Commits: `feat(order): …`, `fix(payment): …`
- Commit message có mã Jira: `<JIRA-123>`
- Không commit trực tiếp lên `<main/develop>`

## Merge request

- [ ] Mô tả: vì sao thay đổi, ảnh hưởng gì, cách kiểm thử
- [ ] Liên kết issue Jira
- [ ] Pipeline xanh, không còn finding critical/high trên CodePulse
- [ ] Ít nhất <1> người duyệt; thay đổi DB/bảo mật cần <tech lead>
- [ ] MR ≤ <400> dòng thay đổi; lớn hơn thì tách

## Quy ước code

| Chủ đề | Quy ước |
|---|---|
| Đặt tên | <camelCase cho biến, PascalCase cho class, tiếng Anh> |
| Xử lý lỗi | <không nuốt exception; log kèm request_id> |
| Log | <không log PII, token, mật khẩu> |
| Truy vấn DB | <luôn có điều kiện tenant; không SELECT *> |
| Test | <mỗi bug fix kèm test tái hiện> |

## Bảo mật bắt buộc

- Không hard-code secret; đọc từ <vault/biến môi trường>
- Kiểm tra quyền ở server cho mọi API, không tin client
- Dữ liệu người dùng đưa vào SQL/HTML/shell phải được tham số hoá/escape

## Định nghĩa "xong" (Definition of Done)

- [ ] Code đã review và merge
- [ ] Test tự động đủ cho thay đổi
- [ ] Tài liệu liên quan đã cập nhật
- [ ] Đã triển khai lên <staging> và kiểm tra
"""

DECISION = """# ADR-<số>: <Quyết định ngắn gọn>

- **Trạng thái**: <Đề xuất | Đã chấp nhận | Thay thế bởi ADR-… | Bỏ>
- **Ngày**: <YYYY-MM-DD>
- **Người quyết định**: <…>

> [!NOTE]
> Một ADR ghi **một** quyết định kỹ thuật có ảnh hưởng lâu dài và lý do tại thời điểm đó. Không
> sửa ADR cũ khi đổi ý: viết ADR mới và đánh dấu ADR cũ là "Thay thế bởi".

## Bối cảnh

<Vấn đề, ràng buộc, áp lực (thời gian, chi phí, đội ngũ, hiệu năng…) dẫn tới cần quyết định.>

## Các phương án đã cân nhắc

| Phương án | Ưu điểm | Nhược điểm |
|---|---|---|
| <A> | <…> | <…> |
| <B> | <…> | <…> |
| <C: giữ nguyên> | <…> | <…> |

## Quyết định

<Chọn phương án nào, phạm vi áp dụng.>

## Hệ quả

- **Tích cực**: <…>
- **Tiêu cực / chấp nhận đánh đổi**: <…>
- **Việc cần làm theo**: <task, migration, đào tạo…>
"""

TEMPLATES: tuple[Template, ...] = (
    Template(
        "overview",
        "Tổng quan dự án",
        "overview",
        "Mục tiêu, thành phần, môi trường, liên hệ",
        OVERVIEW,
    ),
    Template(
        "architecture",
        "Kiến trúc hệ thống",
        "architecture",
        "Bối cảnh, thành phần, luồng chính, dữ liệu",
        ARCHITECTURE,
    ),
    Template(
        "setup",
        "Cài đặt môi trường phát triển",
        "setup",
        "Từ clone đến chạy được trong 1 giờ",
        SETUP,
    ),
    Template("api", "Tài liệu API", "api", "Xác thực, quy ước, mã lỗi, endpoint", API),
    Template("deploy", "Quy trình triển khai", "deploy", "Pipeline, checklist, rollback", DEPLOY),
    Template(
        "runbook",
        "Runbook vận hành và xử lý sự cố",
        "runbook",
        "Mức độ, liên hệ, kịch bản xử lý",
        RUNBOOK,
    ),
    Template(
        "conventions",
        "Quy ước code và quy trình",
        "conventions",
        "Nhánh, MR, quy ước, Definition of Done",
        CONVENTIONS,
    ),
    Template(
        "decision",
        "Bản ghi quyết định kiến trúc (ADR)",
        "decision",
        "Bối cảnh, phương án, quyết định, hệ quả",
        DECISION,
    ),
)
BY_KEY = {t.key: t for t in TEMPLATES}
