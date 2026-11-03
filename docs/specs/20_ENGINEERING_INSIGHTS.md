# 20 – Engineering Insights & Hồ sơ bằng chứng (đánh giá hiệu suất)

> Người dùng yêu cầu 2026-11-03, sau khi phản biện tài liệu "Đề xuất hệ thống đánh giá hiệu suất developer hàng tháng" (DPI). Spec này là **nguồn chuẩn** cho mọi việc về đo hiệu suất kỹ thuật trên CodePulse. Nó không thay spec 08 (điểm review); hai thứ cố ý được giữ tách nhau (§3.2).

---

## 0. Tóm tắt

CodePulse mở rộng thành hai phân hệ, dùng chung collector, identity và phân quyền đang có:

| Phân hệ | Cho ai | Đo gì | Tự động? | Có điểm tổng? |
|---|---|---|---|---|
| **A. Engineering Insights** | Lead, quản lý dự án, CTO | Luồng làm việc (cycle time 4 chặng), cỡ MR, tải review, pipeline, rủi ro tri thức, DORA | Hoàn toàn | **Không.** Chỉ số cấp dự án/repo, có xu hướng |
| **B. Hồ sơ bằng chứng cá nhân** | Dev, lead trực tiếp | Việc đã làm và bằng chứng của một người trong tháng, cho buổi 1:1 | Hệ thống gom, con người bổ sung và kết luận | **Không** (v1) |

Không làm trong v1: điểm 100 hàng tháng cho từng người, phân loại Xuất sắc/Tốt/Đạt, xếp hạng, story point làm sản lượng, LLM chấm comment của người, ra quyết định lương thưởng tự động.

---

## 1. Bối cảnh và lý do chọn hướng này

### 1.1. Tài liệu DPI đề xuất gì
- 5 nhóm tiêu chí: Output 25, Quality 30, Delivery 15, Collaboration 15, Ownership 15.
- Tổng 100 điểm, chấm mỗi tháng, chia 70% tự động và 30% quản lý đánh giá.
- 5 mức xếp loại.
- Hạ tầng: DevLake + SonarQube + Reviewdog + Grafana + một module tự xây.

### 1.2. Những gì giữ lại từ DPI
- Đánh giá đa chiều; không chấm bằng commit, LOC hay giờ hoạt động.
- Tách cycle time theo người chịu trách nhiệm từng chặng.
- Chỉ chấm code mới. Lỗi production phải có RCA trước khi gắn cho người.
- Chạy thử không tính điểm thật; ghi rõ độ tin cậy dữ liệu; mọi con số phải bấm xuống được bằng chứng.
- Không công khai xếp hạng; phân quyền theo phạm vi.

### 1.3. Những gì không theo, và vì sao
1. **Chính các khung DPI trích dẫn khuyên không dùng cho cá nhân.** SPACE, DORA, DX Core 4 và Swarmia đều đặt chỉ số ở cấp team/hệ thống. Một con số tổng cho mỗi người sẽ thành mục tiêu để tối ưu (định luật Goodhart).
2. **"70% tự động" không đúng.** A1 cần người gán độ phức tạp; B2 cần RCA; D1 cần kiểm tra mẫu; B4 phải phán xét rework nào hợp lệ; C2 cần "cam kết đã thống nhất". Phần thật sự tự động chỉ khoảng 25–35 điểm.
3. **Thiếu dữ liệu nền.** CodePulse chưa thu thập issue, deployment, coverage, incident. Áp dụng ngay thì đa số tiêu chí rơi vào trạng thái "không đủ dữ liệu".
4. **Story point không phải sản lượng.** Point là ước lượng tương đối trong một team, sẽ bị thổi phồng khi gắn vào đánh giá.
5. **Tháng quá ngắn cho cấp cá nhân.** Mẫu 8–11 MR/tháng dao động lớn; lỗi production lộ ra sau vài tuần nên điểm đã chốt vẫn phải sửa.
6. **AI review chưa đủ ổn định để làm căn cứ nhân sự.** Demo đo precision và độ lệch khi review lại (Phase 8) chưa chạy. Nếu dev biết finding bị trừ vào đánh giá, AI review mất giá trị coaching.
7. **Kiến trúc trùng lặp.** DevLake + Grafana song song với CodePulse nghĩa là hai collector, hai nơi gắn danh tính, hai bộ phân quyền. Grafana cũng không làm được phân quyền theo từng người mà spec 11 yêu cầu.

### 1.4. Vì sao làm trên CodePulse
Phần khó nhất của DPI (thu thập GitLab, gắn tài khoản về người/team, phân quyền theo dự án, audit) đã có:
- `commits`, `merge_requests`, `mr_commits`, `mr_notes`, `pipelines` (spec 03/04)
- `accounts`/`users`/`team_members`, có lịch sử theo `joined_at`/`left_at` (spec 05)
- `project_grants`, `Principal`, `audit_log` (spec 09/11)

Phân hệ mới chỉ cần thêm phần còn thiếu: danh sách file của commit, deployment, issue, và hồ sơ cá nhân.

---

## 2. Nguyên tắc (bắt buộc; mọi PR thuộc spec này phải tuân theo)

| # | Nguyên tắc | Hệ quả khi thiết kế |
|---|---|---|
| P1 | **Team trước, cá nhân sau** | Mọi chỉ số ở phân hệ A tính theo dự án/repo. Dữ liệu từng người chỉ hiện cho người có quyền xem việc của họ (§8). |
| P2 | **Không điểm tổng cá nhân** | Không có API hay màn hình nào gộp nhiều chỉ số thành một con số cho một người. |
| P3 | **Mọi con số bấm xuống được** | Mỗi chỉ số tổng hợp có API danh sách MR hoặc commit tạo nên nó. |
| P4 | **Định nghĩa có phiên bản** | Công thức nằm ở `app/insights/`, hằng số `INSIGHTS_VERSION` trả kèm mọi response; đổi công thức thì tăng version và ghi vào spec này. |
| P5 | **Ghi rõ khi dữ liệu không đủ** | Mẫu nhỏ, dữ liệu sync cũ hoặc thiếu nguồn → trả `confidence` (§7) chứ không trả số trông như chắc chắn. |
| P6 | **Trung vị và p75, không dùng trung bình** | Thời gian luôn có đuôi dài; trung bình bị vài MR cũ kéo lệch. |
| P7 | **Tường ngăn với điểm review** | `review_score` và finding không đi vào phân hệ A. Phân hệ B chỉ lấy finding đã kiểm chứng và dùng làm *bằng chứng*, không cộng trừ điểm. |
| P8 | **Không tạo động cơ xấu** | Không đếm commit/LOC/comment làm thành tích. Tách MR nhỏ không được thưởng; MR lớn chỉ được *cảnh báo* (khó review), không bị phạt. |

---

## 3. Kiến trúc

### 3.1. Vị trí trong hệ thống
```
GitLab ──► collector (04)  ──► domain: commits, merge_requests, mr_notes, pipelines
                │                         + commit_files (mới, §5.1)
                │                         + deployments, deployment_mrs (mới, §5.2)
                │                         + issues (mới, §5.3, [DECISION D3])
                ▼
        app/insights/            ◄── tính toán thuần (không I/O), có test bảng
          flow.py                    cycle time 4 chặng, cỡ MR, merge không review
          review_load.py             ai review cho ai, độ tập trung
          pipelines.py               tỉ lệ thành công, thời gian chạy
          knowledge.py               bus factor theo module
          dora.py                    (sau khi có deployment)
          confidence.py              mức tin cậy
                ▼
        app/api/routers/insights.py  ──► dashboard /insights (Next.js)
        app/evidence/ (phân hệ B)    ──► dashboard /people/{id}/evidence
```

### 3.2. Tường ngăn với spec 08
- `app/insights/*` **không import** `app/scoring/*`, và ngược lại.
- Phân hệ A không đọc `reviews.score`, `review_findings`, `metrics_*`.
- Phân hệ B chỉ đọc finding có `verified='confirmed'` (spec 19 §4.3) và `status ≠ false_positive`, hiển thị dạng *danh sách bằng chứng*, không có trọng số.

### 3.3. Tính khi xem, không lưu sẵn (v1)
Chỉ số luồng tính trực tiếp từ bảng domain ở mỗi request, giống `/metrics/projects` (`app/api/routers/overview.py`):
- Không có job tổng hợp, nên không phải tính lại khi gắn lại identity hay đổi công thức.
- Giới hạn cửa sổ tối đa 366 ngày.

Nếu p95 thời gian trả về vượt 1,5 giây trên dữ liệu thật thì thêm bảng snapshot (ghi vào IDEAS.md trước, không làm sẵn).

---

## 4. Định nghĩa chỉ số (phân hệ A)

> Mọi mốc thời gian dùng timezone `REPORT_TZ`. Kỳ (tuần ISO, tháng, khoảng tuỳ chọn) dùng chung `app/scoring/aggregate.py` (`period_bounds`, `period_start_of`) và quy tắc khoảng của `overview._window` (từ `RANGE_MIN`, tối đa 366 ngày).

### 4.1. Phạm vi MR
- **MR đã merge trong kỳ:** `merge_requests.state='merged'` và `merged_at ∈ [lo, hi)`.
- Lọc theo dự án (`projects.business_project_id`) hoặc theo repo; chỉ repo `tracked`.
- Không loại MR nào theo nhánh đích. Nhánh đích hiện trong danh sách chi tiết để người xem tự lọc.

### 4.2. Các mốc của một MR
| Mốc | Định nghĩa | Nguồn |
|---|---|---|
| `first_commit_at` | `min(commits.authored_at)` của commit trong MR, bỏ merge commit | `mr_commits` ⨝ `commits` |
| `opened_at` | `merge_requests.created_at_src` | |
| `ready_at` | Lần đầu MR rời trạng thái draft: system note khớp `READY_NOTE` (§4.2.1). Không có thì `ready_at = opened_at` | `mr_notes` |
| `first_response_at` | Phản hồi đầu tiên của người *không phải tác giả*: note thường (không system), **hoặc** system note `approved this merge request`. Comment tóm tắt do CodePulse đăng (chứa `<!-- codepulse-review -->`, spec 06/F7) **không** tính; note của MR đã merge chỉ tính khi trước `merged_at` | `mr_notes` |
| `merged_at` | `merge_requests.merged_at` | |
| `deployed_at` | Lần deploy thành công đầu tiên lên môi trường production có chứa MR (§4.7) | `deployment_mrs` (phase sau) |

#### 4.2.1. Mẫu system note (GitLab ≥ 13; so khớp không phân biệt hoa thường, bỏ dấu `**`)
- `READY_NOTE`: `^marked this merge request as ready$` hoặc `^unmarked as a work in progress$`
- `APPROVED_NOTE`: `^approved this merge request$`

`first_review_at` của spec 04 **giữ nguyên**, vì spec 08 dùng nó. `first_response_at` là định nghĩa riêng của insights: nó đếm cả approve không kèm comment. Đây là cách duyệt MR rất phổ biến mà `first_review_at` bỏ sót.

### 4.3. Bốn chặng (đơn vị giờ, theo lịch, không trừ ngoài giờ làm việc)
| Chặng | Công thức | Người chịu trách nhiệm chính |
|---|---|---|
| **coding** | `opened_at − first_commit_at` | Tác giả |
| **pickup** | `first_response_at − ready_at` | Reviewer / team |
| **review** | `merged_at − first_response_at` | Tác giả + reviewer |
| **deploy** | `deployed_at − merged_at` | Quy trình release |
| `time_to_merge` | `merged_at − opened_at` | (tổng) |

Quy tắc biên:
- Chặng âm (ví dụ commit được rebase với author date sau lúc mở MR, hoặc phản hồi trước `ready_at`) → **0**.
- Thiếu mốc → chặng đó `null` và không vào trung vị.
- MR không có phản hồi nào trước khi merge → pickup và review đều `null`; MR được đếm vào `merged_without_review`.
- Không cắt ngọn ngoại lai. Trung vị/p75 đã chịu được ngoại lai, và danh sách chi tiết sắp theo chặng để tìm ra chúng.

**Giới hạn đã biết** (hiện trong tooltip trên dashboard):
- Giờ tính theo lịch, nên cuối tuần và ngày nghỉ làm chặng dài ra.
- `authored_at` của commit cherry-pick có thể rất cũ, làm coding dài.
- MR chỉ phản hồi bằng emoji thì không ghi nhận được phản hồi.

### 4.4. Cỡ MR
- `size = additions + deletions` sau `IGNORE_PATHS` (đã có trong `merge_requests`).
- Nhóm: XS < 50, S < 200, M < 500, L < 1000, XL ≥ 1000; `null` khi thiếu diff.
- Chỉ số: phân bố theo nhóm, `xl_share` (%), trung vị cỡ.
- Diễn giải: tỉ lệ XL cao là **rủi ro review**, không phải thành tích hay lỗi của người.

### 4.5. Chỉ số khác trên tập MR đã merge
| Chỉ số | Định nghĩa |
|---|---|
| `merged` | Số MR merge trong kỳ |
| `merged_without_review` / `_rate` | MR không có phản hồi nào của người khác trước `merged_at` |
| `review_rounds` | Trung vị và p75 của `merge_requests.review_rounds` |
| Trung vị, p75 mỗi chặng | §4.3 |

### 4.6. MR đang mở (tồn đọng, theo thời điểm hiện tại)
- `state='opened'`, không phải draft (không có system note draft sau lần ready cuối; v1 xấp xỉ: tiêu đề không bắt đầu bằng `Draft:`/`WIP:`).
- `waiting_first_response`: chưa có phản hồi nào; tuổi tính từ lần ready **gần nhất** (MR chuyển lại draft rồi ready thì chờ từ lần sau), không có thì từ `opened_at`.
- Trả về số lượng và top 10 MR chờ lâu nhất.
- Dữ liệu chỉ mới bằng lần sync gần nhất; trả kèm `data_as_of = min(projects.last_synced_at)` của các repo trong phạm vi.

### 4.7. Tải review (`review_load.py`)
- Trên các MR merge trong kỳ: mỗi người (account → user; **mọi** account chưa gắn người gộp thành một dòng "Chưa gắn người") được tính là reviewer của MR nếu có ≥ 1 phản hồi (§4.2) trên MR đó.
- `reviewed_mrs` theo người; `pairs`: top 10 cặp (reviewer → tác giả).
- `top_reviewer_share`: phần trăm MR mà người review nhiều nhất có tham gia. Lớn hơn 60% nghĩa là phụ thuộc một người.
- **Không** đếm số comment, **không** chấm chất lượng comment (P8).
- Danh tính chỉ trả về khi người xem có quyền xem việc của người đó (§8). Ngược lại trả số ẩn danh.

### 4.8. Pipeline (`pipelines.py`)
- Pipeline có `created_at_src ∈ [lo, hi)` của các repo trong phạm vi.
- `success_rate = success / (success + failed)`; bỏ qua `canceled`/`skipped`/`manual`/`running`.
- Trung vị và p75 `duration_sec`.
- Tách theo nhánh mặc định và các nhánh còn lại. Nhánh mặc định đỏ là vấn đề của cả team.

### 4.9. Rủi ro tri thức (`knowledge.py`; cần `commit_files`, §5.1)
- Cửa sổ trượt `KNOWLEDGE_WINDOW_DAYS` (mặc định 180) tính đến hết kỳ đang xem.
- **Module** = `KNOWLEDGE_MODULE_DEPTH` thư mục đầu của đường dẫn (mặc định 2; file nằm ở gốc repo thuộc module `(root)`). Áp `IGNORE_PATHS` và bỏ merge commit.
- `churn(author, module) = Σ (additions + deletions)`.
- **Bus factor** của module = số người tối thiểu mà tổng churn của họ ≥ 50% churn của module (cách định nghĩa phổ biến; ngưỡng `KNOWLEDGE_BUS_THRESHOLD`).
- Cờ:
  - `single_owner`: một người viết **hơn** `KNOWLEDGE_BUS_THRESHOLD` churn (chia đều 50/50 là hai người nắm) **và** module có ≥ `KNOWLEDGE_MIN_COMMITS` (mặc định 10) commit trong cửa sổ. Hoà churn: người đã gắn đứng trước account chưa gắn.
- Độ tin cậy: repo có commit trong cửa sổ nhưng < 80% số commit đó có dữ liệu file → `low` (`partial_file_data`); không repo nào có → `insufficient`.
  - `orphaned`: module cũng có ≥ `KNOWLEDGE_MIN_COMMITS` commit, và người nắm nhiều nhất `users.active=false` hoặc không có commit nào (ở bất kỳ repo nào) trong 60 ngày tính đến hết kỳ.
- Tác giả = người (user) nếu đã gắn; chưa gắn thì theo từng account email git (gộp chung sẽ che mất module một người nắm).
- Diễn giải: một người nắm module **không phải** thành tích của họ, mà là rủi ro của team (P8).

### 4.9b. Thành phần thay cho "N thư mục đầu" (bản 2, người dùng 2026-11-03)
Bản 1 lấy `KNOWLEDGE_MODULE_DEPTH` thư mục đầu, sai với cấu trúc thật. Repo Maven/Spring một module ra `src/main` và `src/test`, tức cả service chứ không phải một module. Repo nhiều module ra `x-service/src`. Ngoài ra, cân theo số dòng đổi thì người import ban đầu, người chạy codegen hay format hàng loạt thành "người nắm". Bản 2 làm theo hai bước: hiểu cấu trúc repo trước, rồi mới đo.

**Bước 1 – Bản đồ thành phần** (`app/insights/components.py`). Mỗi repo một bản, dựng từ các đường dẫn đã có trong `commit_files`. Chỉ dùng đường dẫn, không đọc mã nguồn.

1. Lọc nhiễu, gán `kind=noise` và không tính rủi ro:
   - thư mục build hoặc vendor: `target/`, `build/`, `dist/`, `out/`, `node_modules/`, `vendor/`, `.gradle/`, `.idea/`, `generated/`, `__generated__`;
   - lockfile: `*.lock`, `package-lock.json`, `yarn.lock`, `pnpm-lock.yaml`, `go.sum`;
   - file `*.min.*`, `*.map`, ảnh, font.
2. Gốc build: thư mục chứa `pom.xml`, `build.gradle(.kts)`, `package.json`, `go.mod`, `pyproject.toml`, `setup.py`, `Cargo.toml`, `*.csproj` hoặc `composer.json`. Một file thuộc gốc build sâu nhất chứa nó. Không có gốc nào thì gốc là cả repo.
3. Bỏ bố cục quy ước nằm sau gốc build: `src/main/<ngôn ngữ>/`, `src/test/<ngôn ngữ>/`, `src/main/resources/`, `src/`, `lib/`, `app/`, `internal/`, `pkg/`, `test(s)/`. Sau đó bỏ tiền tố package chung của gốc đó (ví dụ `com/vihat/omicx/account/`). Test được gộp vào **cùng** thành phần với mã nó kiểm tra.
4. Đoạn đầu còn lại là thành phần (tổ chức theo tính năng). Nếu đoạn đó là tên tầng (`controller`, `service`, `repository`, `dto`, `entity`, `model`, `mapper`, `config`, `util`, `impl`, `handler`, `api`, `web`…), tức tổ chức theo tầng, thì thành phần là **tính năng suy ra từ tên file**: bỏ hậu tố tầng, ví dụ `WalletController`, `WalletServiceImpl` và `WalletRepository` đều là `Wallet`.
5. Thành phần có ít hơn 3 file gộp vào "`<gốc>` (khác)". Tên hiển thị là `<tên gốc build> › <thành phần>`.

**Bước 1b – AI gom lại theo nghiệp vụ** (tuỳ chọn; admin bấm theo từng repo):
- Model nhận danh sách thành phần tự động (tên, số file, tối đa 15 đường dẫn mẫu) và tóm tắt hồ sơ dự án nếu có. Model trả về các nhóm nghiệp vụ, mỗi nhóm gồm tên, mô tả, loại (`domain|platform|integration|config|noise`) và **danh sách id thành phần tự động**.
- Model chỉ được gom lại những thành phần đã có, không được tạo đường dẫn mới. Id lạ bị bỏ. Thành phần chưa được nhóm nào nhận thì giữ như bản tự động.
- Điều kiện chạy: `allow_external_llm` của repo khác `false`; còn ngân sách tháng. Chi phí ghi audit với action `insights.components.cost`.
- Kết quả lưu ở `repo_component_maps`. Dựng lại khi admin bấm. Trang báo "cũ" khi số đường dẫn mới chưa có trong bản đồ vượt 20%.

**Bước 2 – Đo**
- Trọng số của mỗi (commit, file) là `0.5^(tuổi_ngày / KNOWLEDGE_HALF_LIFE_DAYS)` (mặc định 90): hiểu biết phai dần theo thời gian.
- Commit chạm hơn `KNOWLEDGE_BULK_FILES` file (mặc định 100) là commit hàng loạt (import, đổi tên, format): mỗi file nhận `(B/n)²` (B = `KNOWLEDGE_BULK_FILES`, n = số file của commit), nên cả commit tính `B²/n` lần chạm. Trọng số liên tục tại ngưỡng, và commit càng lớn càng nhẹ: 1.000 file ≈ 10 lần chạm, 10.000 file ≈ 1.
- **Không dùng số dòng để chia phần nắm.** "Dòng đổi" chỉ để hiển thị, đã bỏ commit hàng loạt và nhiễu.
- Phần nắm của một người = tổng trọng số của họ / tổng trọng số của thành phần. Bus factor, `single_owner` và `orphaned` tính như §4.9 trên phần nắm này.
- Account chưa gắn người vẫn đứng riêng. Nếu người nắm nhiều nhất là account chưa gắn thì không bật cờ `single_owner` mà bật cờ `unattributed` ("gắn account trước khi kết luận").
- File trong `src/main/resources` (migration SQL, template) gom thành một thành phần tài nguyên của module build đó. Tiền tố package chung tính trên mã không phải test. Test đặt riêng (`tests/billing`, `tests/test_billing.py`) được ghép theo thư mục tính năng hoặc tên file.
- AI không được xếp mã vào `noise`: nhóm AI gắn `noise` được coi là `platform` và vẫn được đo. Chỉ quy tắc cố định mới quyết định nhiễu.
- Định nghĩa thay đổi nên `INSIGHTS_VERSION` = 2.
- `KNOWLEDGE_MODULE_DEPTH` chỉ còn dùng khi đường dẫn không khớp quy tắc nào.

### 4.10. DORA (`dora.py`; phase 9.8, cần [DECISION D2])
| Chỉ số | Định nghĩa |
|---|---|
| Deployment frequency | Số deployment `success` lên môi trường production trong kỳ (theo ngày/tuần) |
| Lead time for changes | Trung vị `deployed_at − first_commit_at` của MR được deploy trong kỳ |
| Change failure rate | Tỉ lệ deployment production dẫn tới sự cố. v1 dùng proxy: deployment `failed`, hoặc bị theo sau bởi một deployment của sha cũ hơn trong 24 giờ (rollback). Khi có issue incident ([DECISION D3]) thì dùng incident có liên kết |
| Failed deployment recovery time | Từ deployment hỏng (hoặc thay đổi bị rollback) tới deployment thành công kế tiếp trên cùng môi trường |

Chi tiết khi làm 9.8: rollback = deployment thành công của một sha đã từng deploy thành công trên cùng môi trường, khác sha deploy ngay trước nó; deployment ngay trước đó là thay đổi lỗi. Tính rollback cần lịch sử 90 ngày trước kỳ. Lead time chỉ tính MR mà GitLab ghi là được deployment production mang đi (`/deployments/:id/merge_requests`), ở deployment thành công đầu tiên. Repo không có environment / không đọc được deployment (403/404) thì bỏ qua, không làm hỏng lần sync.

DORA chỉ ở cấp repo/dự án và **không bao giờ** gắn cho một người (đúng khuyến cáo của DORA).

---

## 5. Data model (migration Alembic; tuân theo AGENTS.md)

### 5.1. `commit_files` (phase 9.5)
```sql
CREATE TABLE commit_files (
  sha TEXT NOT NULL REFERENCES commits(sha) ON DELETE CASCADE,
  path TEXT NOT NULL,
  additions INT NOT NULL DEFAULT 0,
  deletions INT NOT NULL DEFAULT 0,
  PRIMARY KEY (sha, path)
);
CREATE INDEX ix_commit_files_path ON commit_files (path);
```
- Converter ghi từ `GlCommit.files` (đã extract sẵn từ `commit_diff`), **sau khi** áp `IGNORE_PATHS`. Upsert theo `(sha, path)`; commit có danh sách file mới thì xoá các path cũ không còn.
- Chỉ lưu đường dẫn và số dòng, không lưu nội dung (AGENTS.md "Cấm").
- Backfill từ `raw_gitlab` hiện có (giữ 365 ngày): CLI `codepulse insights backfill-files [--project ID]`, idempotent.

### 5.2. `deployments` (phase 9.8)
```sql
CREATE TABLE deployments (
  id BIGINT PRIMARY KEY,                       -- GitLab deployment id
  project_id INT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  environment TEXT NOT NULL, environment_tier TEXT,   -- tier từ /environments (D2)
  sha TEXT, ref TEXT,
  status TEXT NOT NULL,                        -- created|running|success|failed|canceled|blocked
  created_at_src TIMESTAMPTZ, finished_at_src TIMESTAMPTZ,
  mr_iids JSONB                                -- MR (iid trong repo) deployment mang đi; NULL = chưa hỏi
);
CREATE INDEX ix_deployments_project_id_finished_at_src ON deployments (project_id, finished_at_src);
ALTER TABLE projects ADD COLUMN production_environment TEXT;   -- ghi đè D2
```
- API GitLab: `GET /projects/:id/environments` (tier), `GET /projects/:id/deployments?updated_after=&order_by=updated_at`, `GET /projects/:id/deployments/:id/merge_requests` (chỉ deployment thành công của production).
- MR lưu dạng iid và nối với `merge_requests` lúc đọc: MR thu thập sau deployment vẫn được tính. Danh sách MR rỗng của deployment vừa xong < 10 phút không lưu (GitLab gắn MR bất đồng bộ), lần sync sau hỏi lại.
- Repo chưa từng có deployment nào được thu thập: lần sync đầu lấy cả cửa sổ backfill. Đổi `production_environment` → xếp hàng sync đầy đủ repo để lấy MR của các deployment cũ. Tier đổi trên GitLab áp cho cả lịch sử của môi trường đó.

### 5.3. `incidents` (phase 9.9, D3)
```sql
CREATE TABLE incidents (
  id BIGINT PRIMARY KEY,                        -- GitLab issue id
  project_id INT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  iid INT NOT NULL, title TEXT, state TEXT,     -- opened|closed
  labels JSONB, web_url TEXT,
  created_at_src TIMESTAMPTZ, closed_at_src TIMESTAMPTZ,
  fix_mr_iids JSONB,                            -- MR (iid) đóng incident (GitLab closed_by)
  UNIQUE (project_id, iid)
);
```
- Thu thập: `GET /projects/:id/issues?issue_type=incident&updated_after=` và `?labels=<nhãn>&updated_after=` (gộp theo id); incident đã đóng hỏi thêm `/issues/:iid/closed_by`.
- DORA (§4.10) thêm: số incident mở trong kỳ, **incident / deploy production**, thời gian khôi phục theo incident (mở → đóng, trung vị/p75), incident đang mở. Không gắn cho người nào.

### 5.4. Hồ sơ bằng chứng (phase 9.10)
```sql
CREATE TABLE evidence_packs (
  id BIGSERIAL PRIMARY KEY,
  user_id INT NOT NULL REFERENCES users(id),
  period_start DATE NOT NULL,                  -- ngày 1 của tháng
  status TEXT NOT NULL DEFAULT 'draft',        -- draft|submitted|reviewed|shared
  snapshot JSONB NOT NULL,                     -- số liệu và bằng chứng hệ thống gom, chốt khi submit
  self_notes TEXT,                             -- dev tự khai: việc ngoài Git, blocker, điều muốn ghi nhận
  lead_notes TEXT,                             -- nhận xét của lead, kèm link bằng chứng
  ai_draft TEXT,                               -- bản nháp AI (§6.4), chỉ là gợi ý
  goals_next TEXT,                             -- mục tiêu tháng sau, chốt trong 1:1
  submitted_at TIMESTAMPTZ, reviewed_by INT REFERENCES users(id), reviewed_at TIMESTAMPTZ,
  shared_at TIMESTAMPTZ, insights_version TEXT NOT NULL,
  UNIQUE (user_id, period_start)
);
CREATE TABLE evidence_items (                  -- bằng chứng dev hoặc lead đánh dấu "đáng ghi nhận"
  id BIGSERIAL PRIMARY KEY,
  pack_id BIGINT NOT NULL REFERENCES evidence_packs(id) ON DELETE CASCADE,
  kind TEXT NOT NULL,                          -- mr|commit|review_given|finding_fixed|incident|design|mentoring|other
  ref TEXT,                                    -- id nội bộ hoặc URL
  title TEXT NOT NULL, note TEXT,
  added_by INT REFERENCES users(id)
);
```

---

## 6. Phân hệ B – Hồ sơ bằng chứng cá nhân

### 6.1. Mục đích
Một trang cho mỗi người, mỗi tháng, dùng trong buổi 1:1. Trang trả lời: tháng này người đó đã làm gì, có bằng chứng gì, gặp vướng gì, tháng sau tập trung vào đâu. **Không** có điểm, không có xếp loại.

### 6.2. Hệ thống tự gom (snapshot)
| Khối | Nội dung | Nguồn |
|---|---|---|
| Việc đã giao | MR đã merge (tên, repo, cỡ, 4 chặng), commit thẳng lên nhánh chính | §4 |
| MR đáng chú ý | Tối đa 5 MR: lớn nhất, chạm module `single_owner`, review nhiều vòng nhất | §4.4, §4.9 |
| Hỗ trợ người khác | MR đã review cho người khác (số lượng + danh sách) | §4.7 |
| Chất lượng | Finding critical/high **đã kiểm chứng** trên việc của mình, số đã sửa (`fixed`), khiếu nại được chấp nhận | spec 19, 08 `fix_rate` |
| Vướng mắc | MR của họ có pickup > p75 của dự án (chờ người khác, không phải lỗi của họ) | §4.3 |
| Độ tin cậy | Account chưa gắn người, repo sync cũ, mẫu nhỏ | §7 |

### 6.2b. Trạng thái và quyền thao tác (chi tiết hoá khi làm 9.10)
| Trạng thái | Ai làm gì | Chuyển sang |
|---|---|---|
| (chưa có) | Hồ sơ "ảo" tính trực tiếp khi mở; lần sửa đầu tiên hoặc job ngày 1 tạo bản ghi | `draft` |
| `draft` | **Bản thân**: sửa `self_notes`, thêm/xoá `evidence_items`, làm mới snapshot, bấm **Gửi** | `submitted` (snapshot chốt) |
| `submitted` | **Lead/admin**: viết `lead_notes`, thêm bằng chứng, **Trả lại** (→ `draft`) hoặc **Đã xem** | `reviewed` |
| `reviewed` | Lead/admin và bản thân: sửa `goals_next`; lead/admin vẫn sửa được `lead_notes`, thêm bằng chứng; lead/admin bấm **Chốt sau 1:1** (không trả về nháp được nữa) | `shared` |
| `shared` | Chỉ đọc; bản thân vẫn **thêm** phản hồi: nối vào cuối `self_notes` kèm ngày, không sửa/xoá được phần đã có (§6.3.5) | — |

- "Lead trực tiếp" = lead của một team người đó **đang là thành viên thường** (`team_members.role ≠ lead`); hai co-lead của cùng team không duyệt hồ sơ của nhau. Lead tự xem hồ sơ của mình như mọi người; không tự duyệt.
- Kỳ = tháng theo `REPORT_TZ`; chỉ mở được tháng đã bắt đầu, không quá `min(24, EVIDENCE_RETENTION_MONTHS)` tháng trước.
- Nháp luôn hiện snapshot tính lại khi mở; gửi thì chốt snapshot tại lúc gửi.
- Mọi thao tác ghi khoá dòng hồ sơ (`SELECT … FOR UPDATE`): hai người thao tác cùng lúc thì người sau thấy trạng thái mới và nhận 409.
- Mọi lần mở hồ sơ hay danh sách tháng của người khác ghi `evidence.view` / `evidence.list`; mọi chuyển trạng thái/sửa ghi `evidence.<hành động>`, sửa sau khi gửi lưu cả nội dung cũ trong audit.
- Job tạo nháp chạy ngày 1–3 hằng tháng (bù lần chạy lỡ), từng người riêng rẽ: một hồ sơ lỗi không làm mất nháp của người khác.
- 9.10b: "MR đáng chú ý" thêm MR chạm module một người nắm (§4.9, cấu hình `KNOWLEDGE_*`); khối **Commit chưa thuộc MR nào** (không nằm trong MR đã thu thập, không phải merge/squash commit của MR; có thể là push thẳng, nhánh chưa mở MR hay commit cũ trước rebase — ngữ cảnh, không phải lỗi; không đưa vào bản nháp AI; tối đa 20); cảnh báo **account chưa gắn người** có commit trong cùng repo (tối đa 5 tên).

### 6.3. Quy trình hàng tháng
1. **Ngày 1–3:** job tạo `draft` cho mỗi người có hoạt động trong tháng trước (scheduler, `REPORT_TZ`).
2. **Dev** đọc, thêm `self_notes` và `evidence_items` (việc ngoài Git: incident, thiết kế, mentoring), rồi bấm **Gửi** → `submitted`. Snapshot được chốt tại lúc này.
3. **Lead trực tiếp** đọc, viết `lead_notes` có link bằng chứng → `reviewed`.
4. **1:1:** hai bên chốt `goals_next` → `shared`. Dev xem được bản cuối; mọi lần sửa sau đó đều ghi audit.
5. Dev không đồng ý một kết luận thì ghi vào `self_notes`; nội dung đó không bị xoá.

### 6.4. Bản nháp AI (task 6.3 cũ)
- Đầu vào: chỉ snapshot (số liệu, tiêu đề MR, tóm tắt finding đã kiểm chứng), **không** gửi code.
- Đầu ra: 3 khối "đã làm / điểm tốt / cần chú ý". Mỗi câu phải trích ít nhất một `ref` trong snapshot; câu nào không có `ref` thì hậu xử lý bỏ đi.
- Chỉ lead thấy bản nháp. Bản nháp không bao giờ tự phát hành.
- Chỉ chạy khi project cho phép LLM ngoài (`allow_external_llm`) theo spec 11.

### 6.4b. Chạy bản nháp (khi làm 9.11)
- `POST …/ai-draft` trả 202 và xếp hàng job `ai_draft_job`; `ai_draft_started_at` giữ hồ sơ (một bản nháp một lúc, 409 nếu đang chạy; job chết quá 10 phút thì nhả). Người được nhận xét gọi → 403.
- Kiểm tra trước khi xếp hàng: đã kết nối Claude, còn ngân sách tháng, mọi repo mà MR và finding trong hồ sơ thuộc về đều `allow_external_llm`.
- Dữ liệu gửi đi đã qua `redact` (che secret) và không đóng được thẻ dữ liệu; job không khoá dòng khi model viết, xong mới khoá và kiểm tra lại hồ sơ vẫn ở `submitted`/`reviewed`, nếu không thì bỏ bản nháp. Chi phí luôn ghi audit `evidence.ai_draft` (tính vào ngân sách).
- Incident (9.9): mỗi lần sync đọc lại incident đang mở mà bộ lọc không còn trả về (`iids[]`): đổi nhãn/loại hay bị xoá thì không còn là incident; mở lại thì bỏ MR sửa cũ; nhãn `Any`/`None` (ký tự đại diện của GitLab) bị bỏ qua.

### 6.5. Quyền
Xem §8. Mặc định đề xuất: **bản thân + lead trực tiếp + admin**. Quản lý dự án và Quản lý tổng **không** xem hồ sơ, vì hồ sơ gộp việc ở mọi dự án của một người ([DECISION D4]).

---

## 7. Độ tin cậy dữ liệu (`confidence.py`)

Mỗi khối chỉ số trả về `confidence: "ok" | "low" | "insufficient"` và danh sách `reasons`:

| Điều kiện | Mức | Lý do hiển thị |
|---|---|---|
| `merged < INSIGHTS_MIN_SAMPLE` (mặc định 5) | `insufficient` (không hiện trung vị) | "Ít hơn 5 MR" |
| Có repo trong phạm vi với `last_synced_at` cũ hơn 48 giờ | `low` | "Repo X sync lần cuối …" |
| > 20% MR có tác giả chưa gắn người (chỉ khối cá nhân) | `low` | "N MR chưa gắn người" |
| Repo chưa từng thu thập danh sách file (`commit_files` trống) | `insufficient` cho §4.9 | "Chưa có dữ liệu file, chạy backfill" |
| Không có deployment production | `insufficient` cho §4.10 | "Repo chưa có deployment" |

Dashboard hiển thị `low` bằng biểu tượng cảnh báo kèm lý do; `insufficient` thay số bằng "—" kèm lý do.

---

## 8. Phân quyền (bổ sung cho spec 09)

| Dữ liệu | Ai xem |
|---|---|
| Chỉ số tổng hợp §4 của một dự án (ẩn danh) | Mọi người được cấp quyền `view`/`manage` dự án đó; admin xem cả repo chưa gán dự án |
| Danh tính trong tải review, rủi ro tri thức, tác giả trong danh sách MR chi tiết | Người có `Principal.can_see_work(author_user_id, bp_id)`, tức bản thân, lead của người đó trong dự án được xem, quản lý dự án, admin. Người khác thấy "một thành viên" |
| Hồ sơ bằng chứng | Bản thân + lead trực tiếp (`lead_members`) + admin ([DECISION D4]) |

Mọi lần mở hồ sơ bằng chứng của người khác đều ghi `audit_log` (`evidence.view`).

---

## 9. API (`/api/v1`, router `app/api/routers/insights.py`)

| Method | Path | Mô tả |
|---|---|---|
| GET | `/insights/flow?business_project_id=&project_id=&period=week\|month\|range&start=&end=` | §4.3–4.6, §4.8: totals, theo repo, xu hướng, MR đang mở, pipeline |
| GET | `/insights/flow/mrs?…cùng bộ lọc…&stage=coding\|pickup\|review\|time_to_merge&only=unreviewed\|xl&limit=&offset=` | Danh sách MR tạo nên số liệu, chặng `stage` chậm nhất trước (MR thiếu chặng đó xuống cuối), mỗi dòng có 4 chặng, cỡ, link GitLab (P3) |
| GET | `/insights/review-load?…cùng bộ lọc…` | §4.7 |
| GET | `/insights/knowledge?business_project_id=&project_id=&end=` | §4.9, §4.9b |
| GET | `/insights/components?project_id=` | §4.9b: cấu trúc repo |
| POST / DELETE | `/admin/insights/components/{project_id}` | §4.9b: AI gom thành phần / về bản tự động (admin) |
| GET | `/insights/dora?…` | §4.10 (phase 9.8) |
| GET | `/evidence/{user_id}` | Các tháng có hồ sơ và trạng thái (9.10) |
| GET/PATCH | `/evidence/{user_id}/{yyyy-mm}` | Hồ sơ một tháng (ảo nếu chưa có); PATCH `self_notes` / `lead_notes` / `goals_next` theo §6.2b |
| POST | `/evidence/{user_id}/{yyyy-mm}/{refresh\|submit\|return\|review\|share}` | Làm mới snapshot (draft) và chuyển trạng thái, có audit |
| POST/DELETE | `/evidence/{user_id}/{yyyy-mm}/items[/{item_id}]` | Thêm hoặc xoá bằng chứng |

Quy ước chung:
- Thiếu cả `business_project_id` lẫn `project_id` thì tính trên mọi dự án người xem được thấy.
- Dự án không được cấp quyền → 404 (theo spec 09).
- Mọi response có `insights_version`, `period_range [from,to)`, `data_as_of`, `confidence`.
- `period=range` dài quá 366 ngày → 422 (§3.3).

---

## 10. Dashboard (spec 10, mục 7 mới)

**Trang `/insights` "Luồng làm việc":**
- Bộ lọc: dự án, repo, kỳ (Tuần/Tháng/Khoảng), giống trang Tổng quan.
- Dải KPI: MR đã merge; trung vị time-to-merge; trung vị pickup; tỉ lệ merge không review; tỉ lệ MR XL; tỉ lệ pipeline thành công. Mỗi KPI có delta so với kỳ trước.
- Biểu đồ cột chồng: trung vị coding / pickup / review theo từng tuần (hoặc tháng).
- Phân bố cỡ MR (XS…XL).
- Bảng theo repo: merged, trung vị các chặng, tỉ lệ không review, pipeline.
- MR đang chờ phản hồi lâu nhất (link GitLab).
- Tải review: thanh theo người, top cặp, cảnh báo phụ thuộc một người.
- Tab **Rủi ro tri thức**: bảng module có bus factor, cờ `single_owner`/`orphaned`, người nắm (theo quyền), số commit.
- Mỗi số bấm vào mở danh sách MR tương ứng. Mỗi khối có tooltip định nghĩa (copy từ §4) và cảnh báo độ tin cậy.
- Chân trang ghi rõ: "Chỉ số cấp team để cải thiện quy trình, không dùng để đánh giá cá nhân."

**Trang hồ sơ bằng chứng** (phase 9.10): `/people/{id}/evidence/{yyyy-mm}`.

---

## 11. Quyền riêng tư, pháp lý, truyền thông
- **Trước pilot:** cập nhật `docs/USER_GUIDE.md`, mục "Insights đo gì và không đo gì". Lead gửi cho team trước khi bật.
- **Phân hệ B** chỉ dùng cho coaching trong v1. Muốn dùng hồ sơ hoặc chỉ số làm căn cứ lương thưởng hay đánh giá "không hoàn thành công việc" thì phải có:
  1. Quy chế đánh giá do công ty ban hành, có tham khảo tổ chức đại diện người lao động (Bộ luật Lao động 2019, Điều 36);
  2. Thông báo xử lý dữ liệu cá nhân (Nghị định 13/2023/NĐ-CP);
  3. Pháp chế và HR duyệt.

  Đây là [DECISION D1], không tự quyết.
- **Retention:** `commit_files` theo `RETENTION_DAYS` cùng `raw_gitlab`. Hồ sơ bằng chứng giữ 24 tháng (cấu hình được), sau đó xoá `self_notes`/`lead_notes`/`ai_draft`, giữ trạng thái.

---

## 12. Đo thành công của chính hệ thống (pilot 2–3 team, 2 tháng)

| Tiêu chí | Ngưỡng để mở rộng |
|---|---|
| Lead dùng trang Insights trong buổi họp cải tiến (retro/planning) | ≥ 2 lần/tháng mỗi team |
| Có hành động cụ thể bắt nguồn từ số liệu (ví dụ đặt SLA pickup, tách MR) | ≥ 1 mỗi team |
| Pickup trung vị hoặc tỉ lệ merge không review cải thiện | Có xu hướng tốt sau 8 tuần |
| Khảo sát dev: "số liệu phản ánh đúng thực tế", "tôi không lo bị đánh giá sai" | ≥ 70% đồng ý |
| Khiếu nại về số liệu sai | Mỗi khiếu nại được truy nguyên và sửa định nghĩa hoặc dữ liệu |

Không đạt thì dừng mở rộng và sửa định nghĩa, không ép dùng.

---

## 13. Lộ trình (Phase 9 trong `14_TASKS_CHECKLIST.md`)

| Task | Nội dung | Phụ thuộc |
|---|---|---|
| 9.1 | Spec này + mục 00/14 | — |
| 9.2 | `app/insights/flow.py` + `pipelines.py` + `confidence.py`: hàm thuần, test bảng | — |
| 9.3 | API `/insights/flow`, `/insights/flow/mrs`, `/insights/review-load` + test quyền | 9.2 |
| 9.4 | Dashboard `/insights` (Luồng làm việc, Tải review) | 9.3 |
| 9.5 | `commit_files` + converter + CLI `insights backfill-files` | — |
| 9.6 | `knowledge.py` + API `/insights/knowledge` + tab Rủi ro tri thức | 9.5 |
| **Demo** | Một dự án thật: 4 chặng 8 tuần gần nhất, top MR chờ, bus factor | 9.2–9.6 |
| 9.7 | USER_GUIDE "Insights đo gì" + pilot 2–3 team (§12) | Demo |
| 9.8 | Deployments collector + DORA | [D2] |
| 9.9 | Issues / incident | [D3] |
| 9.10 | Hồ sơ bằng chứng (model, job draft, API, UI, audit) | [D1], [D4], pilot 9.7 |
| 9.11 | Bản nháp AI cho hồ sơ | 9.10 |

Thứ tự này đảm bảo: có giá trị cấp team trước (9.2–9.6, không cần quyết định nào), học được từ pilot rồi mới làm phần cá nhân (9.10).

---

## 14. `[DECISION]` – đã chốt (người dùng giao chọn phương án tối ưu về quản lý, 2026-11-03)
- [x] **D1** Phân hệ B **chỉ dùng cho coaching / 1:1** ở v1: không điểm, không xếp loại, màn hình ghi rõ "không dùng làm căn cứ lương thưởng". Muốn dùng cho lương thưởng phải có đủ §11 (quy chế, HR, pháp chế) và một quyết định mới. *Lý do:* dev chỉ khai thật việc ngoài Git và vướng mắc khi tin rằng hồ sơ không bị dùng để phạt; gắn lương ngay sẽ biến hồ sơ thành chỗ tô vẽ.
- [x] **D2** Môi trường production = environment có **`tier = production`** của GitLab (GitLab tự gán theo tên `production`/`prod`/`live`…); không có tier thì so tên `production`, `prod`. Admin ghi đè theo repo bằng `projects.production_environment`. *Lý do:* không bắt ai cấu hình trước, vẫn sửa được ngoại lệ.
- [x] **D3** (cập nhật 2026-11-03, người dùng yêu cầu làm đủ) 9.9 làm **bản tối thiểu, chỉ incident trên GitLab**: issue có type `incident` hoặc nhãn trong `INCIDENT_LABELS` (mặc định `incident`); MR sửa = MR GitLab ghi là đóng issue (`closed_by`). Không tích hợp Jira, không đếm task. Trước đó: hoãn 9.9. CFR v1 dùng tín hiệu deployment (§4.10: failed, rollback). Không làm tích hợp tracker trước khi pilot cho thấy cần; nếu làm thì chỉ để nối incident ↔ MR, không bao giờ đếm task/story point. *Lý do:* chưa có quy ước tracker chung; đếm task là đúng cái spec loại (§1.3.4).
- [x] **D4** Hồ sơ bằng chứng: **bản thân + lead trực tiếp (lead của team người đó đang thuộc) + admin**. Quản lý dự án, Quản lý tổng, HR **không** xem. *Lý do:* hồ sơ gộp việc ở mọi dự án và chứa nhận xét 1:1; càng ít người đọc, nội dung càng thật. Mở rộng cho HR chỉ khi D1 đổi.
- [x] **D5** Độ sâu module mặc định 2 (`KNOWLEDGE_MODULE_DEPTH`). Bản 2 (2026-11-03): thay bằng bản đồ thành phần §4.9b; độ sâu chỉ còn là dự phòng.

Thứ tự làm sau khi chốt: 9.10 (hồ sơ) → 9.8 (DORA) → 9.11 (bản nháp AI, sau pilot vì tốn LLM và cần hồ sơ thật để đánh giá).

---

## 15. Test bắt buộc
- [ ] **flow:** bảng test cho từng chặng gồm MR draft rồi ready; approve không comment; phản hồi của chính tác giả (không tính); commit rebase làm chặng âm (→ 0); MR không review; thiếu `merged_at`.
- [ ] **Trung vị / p75:** số phần tử chẵn/lẻ, bỏ `null`, mẫu dưới `INSIGHTS_MIN_SAMPLE` → `insufficient`.
- [ ] **Ranh giới kỳ** theo `REPORT_TZ` (MR merge 23:30 giờ VN ngày cuối tháng).
- [ ] **review_load:** người review nhiều MR được đếm một lần mỗi MR; tác giả tự comment không thành reviewer; account chưa gắn người.
- [ ] **knowledge:** bus factor 1/2/3; `IGNORE_PATHS`; merge commit bị loại; file ở gốc repo; cờ `orphaned`.
- [ ] **Quyền (API):** dự án không được cấp quyền → 404; người `view` không thấy danh tính người ngoài team; quản lý dự án thấy; admin thấy repo chưa gán.
- [ ] **commit_files:** convert hai lần không nhân đôi; backfill idempotent.
- [ ] **Tường ngăn:** test import đảm bảo `app.insights` không phụ thuộc `app.scoring` và ngược lại.

---

## Phụ lục A – Đối chiếu với tiêu chí DPI

| Tiêu chí DPI | Xử lý ở đây |
|---|---|
| A1 Complexity-adjusted delivery | **Không làm.** Story point không dùng làm sản lượng. MR/việc đã giao hiện trong hồ sơ bằng chứng như danh sách, không quy đổi điểm |
| A2 Completed contributions | `evidence_items` (dev/lead đánh dấu) |
| A3, B5, E1–E3 Đánh giá của lead | `lead_notes` có bằng chứng, không chấm thang điểm (v1) |
| B1 Quality gate | Spec 07 (Semgrep/Gitleaks) + AI review đã kiểm chứng, vào hồ sơ như bằng chứng |
| B2 Escaped defects | Chỉ ở cấp team qua DORA CFR (9.8). Gắn cá nhân khi đã có RCA ([D3]) |
| B3 Test adequacy | Ngoài phạm vi v1 (chưa có coverage theo MR) |
| B4 Maintainability & Rework | Chưa làm. Ứng viên sau pilot: churn 21 ngày trên `commit_files` (IDEAS) |
| C1 Flow efficiency | §4.3, **cấp team** |
| C2 Predictability | Cần issue có due date ([D3]); không làm trước pilot |
| C3 Process compliance | `merged_without_review`, pipeline: cấp team |
| D1 Meaningful review | Chỉ đếm *số MR đã review cho người khác* (§4.7); không chấm chất lượng comment |
| D2 Knowledge sharing | §4.9 cho thấy rủi ro; đóng góp chia sẻ vào `evidence_items` |
| Report 1 Executive | Trang `/insights` toàn bộ dự án (không có phân bố xếp loại người) |
| Report 2 Scorecard | Thay bằng hồ sơ bằng chứng (không điểm) |
| Report 3 Code quality | Trang Tổng quan dự án hiện có + spec 07 |
| Report 4 Delivery & DORA | `/insights` + 9.8 |
| Report 5 Collaboration & Knowledge risk | Tải review + Rủi ro tri thức |
| Report 6 Competency & Growth | Ngoài v1; xem lại sau khi có ≥ 6 tháng hồ sơ |
