# 25 – Đánh giá hiệu suất Developer hàng tháng (DPI scorecard)

> Ngoài spec gốc, theo yêu cầu người dùng (2026-11-04): "D:\tmp\codepulse-upgrade-plan.md là plan update, hãy đọc, phân tích và nâng cấp theo đây". Plan đối chiếu tài liệu *Đề xuất hệ thống đánh giá hiệu suất Developer hàng tháng* (**DPI**): **70 điểm chỉ số kỹ thuật tự động + 30 điểm đánh giá của quản lý**, 5 nhóm A–E. Các mục **[Quyết định]** do agent chọn theo uỷ quyền của người dùng (xem memory "decisions-delegated"), ở góc độ một engineering manager.

## 0. Phân tích plan

### 0.1 Đối chiếu với nguyên tắc đang có
- Spec 11 và spec 20 giữ nguyên tắc "điểm là tín hiệu coaching", "chỉ số luồng không chấm cá nhân", "hồ sơ tháng không có điểm". DPI **cố ý** đưa điểm cá nhân vào đánh giá tháng. **[Quyết định]** Làm theo plan, nhưng giữ các rào chắn của plan và của hệ thống:
  - mọi điểm DPI chạy **shadow** cho tới khi admin tắt cờ (§6.4); nhãn "Chạy thử" hiện trên mọi scorecard;
  - chỉ số luồng C1 chỉ tính **thời gian của chính tác giả**, không tính thời gian chờ người khác (plan §P2.2);
  - không có bảng xếp hạng toàn công ty; người xem chỉ thấy người trong phạm vi quyền (§7);
  - mỗi chỉ số có định nghĩa, nguồn dữ liệu, liên kết bằng chứng và độ tin cậy; tổng độ tin cậy thấp thì **không xếp loại**.
- Những phần đã có và được dùng lại: điểm review theo bộ tiêu chí dự án (spec 22) cho B1; finding loại `testing` cho B3; luồng MR (spec 20 §4) cho C1, C3, D1, D2; hồ sơ tháng (spec 20 §5.4) làm bước **developer tự khai**; khiếu nại finding (spec 09) mở rộng thành khiếu nại chỉ số; bản nháp AI của hồ sơ tháng làm **AI nháp nhận xét tháng**.

### 0.2 Phần nào làm được trong code, phần nào là quy trình
| Phase | Trong code | Ngoài code (đội phải làm) |
|---|---|---|
| P0 | Trang **Sẵn sàng dữ liệu** (từng repo: nhánh mặc định có bảo vệ không, % thay đổi qua MR, có pipeline / coverage, % issue có `complexity::`, dự án đã gán bộ tiêu chí chưa); nút **Tạo label chuẩn** trên GitLab; mẫu CI, mẫu MR trong Hướng dẫn | Bật protected branch, sửa CI xuất coverage/junit, gán bộ tiêu chí, quy ước label |
| P1 | Collector issue, liên kết issue ↔ MR, coverage + test report của pipeline, rework 21 ngày, loại bot | — |
| P2 | Rubric DPI có phiên bản, profile theo level, điểm tháng + rolling, 5 mức, độ tin cậy, snapshot | Hiệu chỉnh target sau 2 tháng shadow |
| P3 | Đánh giá 30 điểm, peer feedback ẩn danh, quy trình 5 bước có hạn, calibration, khiếu nại chỉ số, cờ shadow, phân quyền | Chạy pilot 2–3 team |
| P4 | R1 Executive, R2 Scorecard + drill-down, R6 Growth, escaped defects + RCA, phân loại comment review (D1), AI nháp nhận xét | — |

- **[Quyết định]** P1.5 (SonarQube / Code Quality report) **chưa làm**: plan ghi "tuỳ chọn", Sonar Community không phân tích MR; B1 dùng điểm review AI + pipeline + quality gate do CodePulse tính (§3). Ghi vào IDEAS.md.
- **[Đã chốt 2026-11-05]** Jira: nguồn task là **Jira Data Center** hoặc **GitLab Issues**, Admin chọn (§6.3). Chi tiết ở `27_JIRA.md`.

## 1. Mô hình điểm
- **Rubric** (`dpi_rubrics`, có phiên bản, bản mặc định `dpi-v1`): 17 chỉ số trong 5 nhóm, mỗi chỉ số có `max`, `source` (`auto` | `manager`) và mô tả. Tổng `max` tự động = 70, của quản lý = 30.
- **Profile** (`dpi_profiles`): trọng số 5 nhóm (tổng 100) và target cho A1. Mặc định: `junior`, `middle`, `senior`, `tech_lead` (bảng DPI mục 7), `devops_sre`, `qa_automation`, `ai_ml`.
  - **[Quyết định]** Ba profile chuyên biệt dùng trọng số của `middle` (A 25 / B 30 / C 15 / D 15 / E 15) và cùng định nghĩa chỉ số; A1 của họ tính trên issue mang `type::automation|incident|chore` như mọi issue. Admin chỉnh được trọng số và target.
- **Người ↔ profile** (`dpi_user_profiles`): profile theo `effective_from` (có lịch sử); người chưa gán dùng `middle` và gắn cờ "chưa gán level".
- **Cách tính**:
  1. Mỗi chỉ số cho một giá trị chuẩn hoá `v ∈ [0, 1]` (hoặc *không có dữ liệu*) và độ tin cậy.
  2. Điểm nhóm = trọng số nhóm của profile × Σ(v × max) / Σ(max) trên các chỉ số **có dữ liệu** của nhóm.
  3. Tổng = Σ điểm nhóm / Σ trọng số các nhóm có dữ liệu × 100.
  4. **Độ phủ** = Σ max của chỉ số có dữ liệu / 100 (theo trọng số profile).
  - **[Quyết định]** Chỉ số không có dữ liệu **không bị tính 0** (không phạt người vì đội chưa có CI), nhưng làm giảm độ phủ và độ tin cậy.
- **Tạm tính / chính thức**: trước khi lead chấm, 30 điểm của quản lý chưa có: scorecard là **tạm tính** (chỉ phần tự động, ghi rõ).
- **Rolling** `R = 0.5·S0 + 0.3·S-1 + 0.2·S-2`; tháng thiếu thì chia lại trọng số trên các tháng có điểm và gắn cờ "thiếu tháng".
- **Xếp loại**: 90–100 Xuất sắc · 80–89 Tốt · 70–79 Đạt · 60–69 Cần cải thiện · < 60 Không đạt kỳ vọng.
- **Độ tin cậy** mỗi chỉ số: `high | medium | low` theo cỡ mẫu (§2). Tổng: `high` khi độ phủ ≥ 80% và không chỉ số nặng nào `low`; `medium` khi độ phủ ≥ 60%; còn lại `low` → **"Chưa đủ dữ liệu"**, không xếp loại.

## 2. Chỉ số tự động (bản khởi đầu; hiệu chỉnh sau shadow)
Tháng theo `REPORT_TZ`. "Của người đó": tài khoản Git gắn với người (spec 05), trừ **bot** (§4.3), merge commit, file trong `IGNORE_PATHS`.

| Mã | Max | Công thức `v` | Nguồn / bằng chứng | Độ tin cậy |
|---|---|---|---|---|
| A1 Complexity-adjusted delivery | 15 | `min(1, Σ điểm complexity của issue đóng trong tháng mà người đó là assignee / target)`. `complexity::XS/S/M/L/XL` = 1/2/3/5/8; issue nhiều assignee chia đều. Target = target của profile × ngày làm việc khả dụng / 20 (ngày nghỉ khai ở bước tự khai). **[Quyết định]** target mặc định junior 20, middle 28, senior 32, tech_lead 20 điểm/tháng | issue | `low` khi < 2 issue có complexity, hoặc < 50% issue đóng có complexity |
| A2 Completed contributions | 5 | Số đóng góp **đã xác nhận** trong tháng: issue đóng có `type::*` (một issue tính một lần dù nhiều MR), cộng mục tự khai được lead duyệt. 0 → 0; 1–2 → 0,4; 3–5 → 0,7; ≥ 6 → 1 | issue, mục tự khai | `medium` khi chỉ có mục tự khai |
| B1 Quality Gate Compliance | 8 | `0.5 × điểm review TB (theo bộ tiêu chí chính của dự án, không có thì điểm review) / 100 + 0.25 × tỉ lệ pipeline thành công + 0.25 × tỉ lệ review không có critical/high được tính`; phần thiếu dữ liệu thì chia lại trọng số | review, pipeline | `low` khi < 3 review |
| B2 Escaped Defects | 8 | `1 − Σ phạt / 8` (P1 = 4, P2 = 2, P3 = 1) cho lỗi production **đã RCA xác nhận** gán cho người đó (theo tỉ lệ trách nhiệm), sàn 0 | lỗi production (§4.4) | `high` |
| B3 Automated Test Adequacy | 5 | `0.6 × min(1, coverage / 80%) + 0.4 × (1 − min(1, finding testing / 100 LOC))`; coverage = coverage của pipeline trên head các MR của người đó (TB). Không có coverage: chỉ phần finding | pipeline, finding | `low` khi không có coverage |
| B4 Maintainability & Rework | 4 | Rework 21 ngày = dòng người đó xoá lại trong **cùng file** trong 21 ngày sau khi thêm / dòng đã thêm (mức file, §4.2). ≤ 10% → 1; ≥ 40% → 0; tuyến tính ở giữa. Commit `refactor:` hoặc MR `type::refactor` không tính | commit_files | `low` khi < 200 dòng thêm |
| C1 Development Flow Efficiency | 5 | Thời gian **của tác giả** trên mỗi MR = viết code (commit đầu → mở MR) + xử lý góp ý (góp ý → push tiếp theo). Trung vị của người đó / trung vị của repo (90 ngày): ≤ 1 → 1; ≥ 2,5 → 0 | MR, ghi chú | `low` khi < 3 MR |
| C2 Delivery Predictability | 6 | % issue có hạn (due date, hoặc due date của milestone) được giao cho người đó, hạn rơi vào tháng, đóng đúng hạn. Issue có label `blocked` không tính | issue | `low` khi < 3 issue |
| C3 Process Compliance | 4 | % "đơn vị thay đổi" đạt: MR merge có link issue + checklist không còn ô trống + pipeline head thành công + có review trước merge. **Commit đẩy thẳng lên nhánh mặc định là một đơn vị không đạt** | MR, commit | `low` khi < 3 đơn vị |
| D1 Meaningful Review Contributions | 6 | Comment review của người đó trên MR người khác được phân loại `correctness / security / design / testability / actionable` **và được xác nhận** (tác giả push sau comment, hoặc lead tick). `min(1, số comment / 10)`; trần 10 để chống spam | ghi chú MR, phân loại (§4.5) | `low` khi < 3 MR được review |
| D2 Knowledge Sharing | 4 | `min(1, (MR review ở module mình không commit 90 ngày qua + 2 × mục tự khai loại design/mentoring được duyệt) / 6)` | MR, mục tự khai | `medium` |

## 3. Đánh giá của quản lý (30 điểm)
- Lead chấm **A3 (5), B5 (5), D3 (5), E1 (5), E2 (5), E3 (5)**; mỗi ô gồm điểm, nhận xét và **≥ 1 bằng chứng** (link MR, issue, tài liệu, incident) — thiếu bằng chứng thì không lưu được.
- **B5**: hệ thống gợi ý 3–5 MR mẫu của tháng (lớn nhất, nhiều finding critical/high nhất, điểm review thấp nhất).
- **D3**: peer feedback tuỳ chọn (1–5 + nhận xét), **ẩn danh với người được đánh giá**; lead thấy người viết; tối đa 1 feedback mỗi cặp người mỗi tháng; lead tổng hợp vào D3.
- Tên chỉ số: A3 Business/Technical impact, B5 Code design quality, D3 Teamwork, E1 Ownership & accountability, E2 Learning & growth, E3 Initiative & mentoring.

## 4. Dữ liệu (P1)
### 4.1 Issue và liên kết
- Bảng `issues` (mọi issue của repo đang theo dõi, cập nhật từ lần sync trước): assignee, labels, `type`, `complexity`, `complexity_points`, `prod_severity`, milestone, due date (của issue, không có thì của milestone), state, closed_at, time_stats.
- Bảng `issue_mr_links(issue_id, mr_id, source)`; `source`: `closes` (GitLab closed_by, hoặc `Closes/Fixes/Resolves #n` trong mô tả MR), `mention` (`#n` khác trong mô tả), `branch` (nhánh `123-...` hoặc `feature/123-...`).
- MR có thêm `labels`, `description_tasks_total`, `description_tasks_done` (ô checklist `- [ ]` / `- [x]` trong mô tả; **không lưu mô tả**).
### 4.2 Pipeline, rework
- Pipeline có thêm `coverage` (GitLab tính từ job coverage regex) và `tests_total/failed/skipped` (`/pipelines/:id/test_report_summary`), `source`.
- **[Quyết định]** Rework tính ở **mức file** từ `commit_files` (không blame từng dòng: tốn và cần nội dung code, trái spec AGENTS "không lưu nội dung file"): lượng xoá của cùng tác giả trong cùng file trong 21 ngày sau một lần thêm, tối đa bằng lượng đã thêm.
### 4.3 Bot và code tự sinh
- `BOT_ACCOUNT_PATTERN` (regex, mặc định `(^|[-_.])bot([-_.@]|$)|renovate|dependabot|codepulse|gitlab-ci`): tài khoản khớp không tính vào chỉ số nào. Merge commit và `IGNORE_PATHS` (thêm mặc định `**/openapi/**`, `*.generated.*`, `package-lock.json`, `yarn.lock`, `go.sum`) như trước.
### 4.4 Lỗi production (B2)
- Issue có `prod-bug::P1|P2|P3` thành **lỗi production** (`prod_defects`). Gợi ý commit/MR gốc: MR đóng issue → commit sửa; commit `Revert "..."` / `This reverts commit <sha>`; mô tả hotfix nhắc SHA. Người gây ra chỉ là gợi ý.
- **Lead xác nhận RCA** trước khi tính: chọn người chịu trách nhiệm và tỉ lệ (mặc định 100% tác giả commit gốc, có thể chia), ghi **lỗ hổng quy trình** (thiếu test, review sót, thiếu môi trường…). Có thể kết luận "không quy cho cá nhân" (lỗi quy trình): không trừ ai.
### 4.5 Phân loại comment review (D1)
- Job tháng: comment không phải hệ thống, của người không phải tác giả MR, dài ≥ 15 ký tự; Claude gắn nhãn `actionable | design | correctness | security | testability | cosmetic | other` (theo lô, tính vào ngân sách LLM, bỏ qua repo không cho gửi ra LLM ngoài).
- **[Quyết định]** Không gọi được LLM thì dùng luật: comment ≥ 40 ký tự có từ khoá (bug, lỗi, null, race, test, security, nên tách…) là `actionable`, còn lại `other`.
- "Được xác nhận": tác giả MR push commit sau comment đó (ghi chú hệ thống "added N commits"), hoặc lead tick trên scorecard.

## 5. Quy trình tháng (P3)
```
draft (ngày 1 tháng N+1) → self_review (hạn N+3) → lead_review (hạn N+7)
→ calibration (hạn N+10) → finalized (sau 1:1) → locked
```
- **draft**: job ngày 1 tính scorecard tạm tính cho mọi người đang hoạt động có ít nhất một hoạt động Git trong tháng, tạo kỳ đánh giá.
- **self_review**: developer dùng **hồ sơ tháng** (spec 20 §5.4) để chọn đóng góp tiêu biểu, khai việc ngoài Git, blocker, **số ngày nghỉ** (điều chỉnh target A1). Bấm "Gửi" chuyển bước.
- **lead_review**: lead chấm 30 điểm, duyệt từng mục tự khai (duyệt thì A2/D2 được tính), tick D1 nếu cần, xác nhận RCA.
- **calibration**: người có `dpi.calibrate` xem phân phối theo team/profile, cảnh báo team lệch (TB team lệch TB chung > 10 điểm, hoặc > 1 độ lệch chuẩn), điều chỉnh ±10 điểm **bắt buộc ghi lý do**.
- **finalized**: lead chốt sau 1:1 (ghi ngày 1:1 và action plan tháng sau); **locked**: tự khoá sau 7 ngày, hoặc admin khoá. Bản khoá không sửa được; chấm lại tạo **snapshot mới**, bản cũ giữ.
- Quá hạn thì không chặn: bước vẫn mở, scorecard ghi "quá hạn" để lead thấy.
- **Khiếu nại chỉ số**: người được đánh giá khiếu nại một chỉ số (lý do ≥ 10 ký tự) trước khi khoá; lead / người calibrate trả lời (chấp nhận → điều chỉnh điểm chỉ số đó kèm lý do, hoặc từ chối).

## 6. Vận hành
1. **Snapshot**: mỗi lần tính lưu `dpi_snapshots(user, month, rubric_version, profile, inputs_hash, values, confidence, total, rating, kind)` (`kind`: `auto` | `final`); tính lại cùng đầu vào cùng phiên bản cho cùng kết quả (kiểm bằng test). Mọi lần xem scorecard người khác, chấm, điều chỉnh, khoá ghi audit.
2. **Chống gaming**: không cộng theo số MR/commit; cảnh báo **tách MR bất thường** (≥ 3 MR merge cùng một issue trong một ngày của một người); trần D1; so sánh chỉ trong cùng team và profile; không có bảng xếp hạng toàn công ty.
3. **Nguồn task**: `app_settings["dpi_task_source"]` = `gitlab` | `jira` (mặc định `jira` khi đã cấu hình Jira). DPI chỉ đọc issue của nguồn đang chọn; khi là Jira, collector GitLab ngừng lấy issue. Xem `27_JIRA.md`.
4. **Shadow**: cờ `dpi_shadow` (trang **DPI hàng tháng › Cấu hình**, thẻ "Chạy thử (shadow)", nút "Tắt chạy thử" / "Bật lại chạy thử" chỉ hiện với quyền `dpi.admin`; không nằm ở trang Admin — sửa 2026-11-13), mặc định **bật**. Khi bật, mọi scorecard và báo cáo ghi "Chạy thử — không dùng cho lương thưởng". Tắt cờ là quyết định của quản lý, có audit.
5. **[Quyết định] 70/30 đồng nhất** (đúng tổng max của rubric); profile đổi trọng số **nhóm**, không đổi tỉ lệ tự động/quản lý trong nhóm. Muốn tăng phần quản lý cho Senior/TL thì sửa rubric (phiên bản mới).
6. **[Quyết định] Manager (team)** chỉ thấy **phân phối điểm** của team (không thấy từng người), như chính sách hiện tại; người điều chỉnh từng người cần `dpi.calibrate` (Quản lý tổng có sẵn, EM được admin cấp). **HR** dùng quyền `dpi.final_view`: chỉ bản `finalized/locked`.

## 7. Quyền (spec 21)
| Quyền | Phạm vi | Mặc định |
|---|---|---|
| `dpi.view` (sensitive) | own / team / project / all | member own, lead team, quản lý dự án project, quản lý tổng all |
| `dpi.self` | own | member |
| `dpi.dispute` | own | member |
| `dpi.assess` (sensitive) | team / project / all | lead team |
| `dpi.distribution` | team / all | manager team, quản lý tổng all |
| `dpi.calibrate` (sensitive) | team / project / all | quản lý tổng all |
| `dpi.final_view` (sensitive) | flag | — (cấp cho HR) |
| `dpi.defects` | team / project / all | lead team, quản lý dự án project, quản lý tổng all |
| `dpi.admin` | flag | — (admin) |

## 8. Báo cáo (P4)
- **R1 Tổng quan DPI** (`/dpi`): điểm DPI TB, phân bổ 5 mức, TB C2 (delivery predictability), TB phần pipeline/quality gate của B1, trung vị thời gian tác giả (C1), độ phủ dữ liệu; lọc team / dự án / profile / tháng. Danh sách người chỉ trong phạm vi quyền.
- **R2 Scorecard** (`/dpi/<người>?month=`): 5 nhóm, mỗi chỉ số: điểm, `v`, độ tin cậy, công thức, **drill-down** tới issue / MR / pipeline / finding / lỗi production; trạng thái quy trình và việc cần làm; action plan; nhãn shadow, tạm tính, chưa đủ dữ liệu.
- **R6 Competency & Growth**: trên scorecard, xu hướng 6 tháng của tổng và 5 nhóm, rolling, mức xếp loại.
- R3 (chất lượng, debt), R4 (DORA, luồng), R5 (ai review cho ai, bus factor) dùng **Luồng làm việc** (spec 20) đã có; thêm vào R3 coverage TB và tỉ lệ pipeline thành công theo repo trên trang Sẵn sàng dữ liệu.
- **AI nháp nhận xét tháng**: bản nháp AI của hồ sơ tháng nhận thêm scorecard; chỉ phát hành sau khi lead duyệt (như hiện tại).

## 9. P0 — mẫu cho đội
- **Label** (nút "Tạo label chuẩn"): `complexity::XS|S|M|L|XL`, `type::feature|bug|refactor|chore|docs|incident|automation`, `prod-bug::P1|P2|P3`, `blocked`.
- **Mẫu MR** (`.gitlab/merge_request_templates/Default.md`):
  ```markdown
  Closes #

  ## Checklist
  - [ ] Đã liên kết issue
  - [ ] Có test cho thay đổi
  - [ ] Migration (nếu có) chạy được hai chiều
  - [ ] Có cách rollback
  ```
- **Coverage trong CI** (GitLab đọc `coverage:` regex và báo cáo `coverage_report` / `junit`):
  ```yaml
  test:
    script: [mvn -B verify]          # JaCoCo: target/site/jacoco/jacoco.xml
    coverage: '/Total.*?([0-9]{1,3})%/'
    artifacts:
      reports:
        junit: target/surefire-reports/TEST-*.xml
        coverage_report: {coverage_format: jacoco, path: target/site/jacoco/jacoco.xml}
  ```
  Go: `go test -coverprofile=c.out ./... && go tool cover -func c.out` với `coverage: '/total:\s+\(statements\)\s+(\d+.\d+)%/'`; Jest/Vitest: `--coverage --coverageReporters=text-summary cobertura` với `coverage: '/All files[^|]*\|[^|]*\s+([\d\.]+)/'`.
- **Protected branch**: Settings › Repository › Protected branches: `main`/`master`/`develop`: Allowed to push **No one**, Allowed to merge **Maintainers**; Settings › Merge requests › Approvals: ≥ 1, "Prevent approval by author".

## 10. Nghiệm thu (plan §5) và việc của con người
1–3 (MR ≥ 80%, pipeline ≥ 90% repo, issue có complexity ≥ 70%) đo trên trang **Sẵn sàng dữ liệu**. 4 (scorecard ngày 1, drill-down) và 6 (phiên bản, tính lại cho cùng kết quả) có test. 5 (pilot 2 tháng shadow trên 2–3 team) là việc của đội: chọn 2–3 team để đối chiếu (không có màn hình chọn team pilot: cờ shadow áp dụng cho mọi team, giữ bật), sau mỗi tháng so điểm hệ thống với đánh giá của lead ở tab **DPI hàng tháng › Hiệu chỉnh** (cột "Lệch so với lead").

## 11. Sau review và vận hành (2026-11-04)
- **Chạy lại dữ liệu DPI** (người dùng: "cần có nút chạy lại dữ liệu, chứ giờ lên làm gì có dữ liệu"): nút ở DPI › Tổng quan (quyền `dpi.admin`). Chọn 1 / 3 / 6 tháng gần nhất và có đồng bộ lại GitLab trước hay không (mặc định có: lần đầu bắt buộc, vì các lần sync cũ chưa lấy issue, checklist MR, coverage, test report). Job nền `dpi_rebuild_job`: đồng bộ toàn bộ repo đang theo dõi trong cửa sổ backfill (repo lỗi bỏ qua, ghi lại), rồi tính scorecard từng tháng từ cũ tới mới; tiến độ (repo / tháng đang làm) hiện ngay trên nút; một lượt một lúc (409 khi đang chạy).
- **Xem không ghi gì**: mở scorecard chỉ tính và hiển thị; kỳ đánh giá và snapshot được tạo bởi job ngày 1, nút chạy lại, và các bước của quy trình. Tháng chưa kết thúc chỉ để xem: không tự khai, chấm, khiếu nại được.
- **Thứ tự**: lead chỉ chấm sau khi người được đánh giá gửi tự khai, hoặc khi đã qua hạn tự khai (ngày 3).
- **Rubric gắn với kỳ đánh giá** khi kỳ mở (`dpi_reviews.rubric_version`): đổi rubric áp dụng từ kỳ sau.
- **HR** (`dpi.final_view`) chỉ thấy bản đã chốt: xu hướng chỉ gồm tháng đã chốt, không thấy khiếu nại, phản hồi đồng nghiệp, nhận xét của lead (chỉ điểm).
- **Phân phối cho manager**: ẩn team dưới **5** người, không tính chính người xem, thành viên theo tháng đó.
- **Phản hồi đồng nghiệp**: dưới 3 phản hồi thì người được đánh giá chỉ biết số lượng (ẩn điểm TB và nội dung); chỉ cho tháng này hoặc tháng đã qua, không cho kỳ đã chốt.
- **Lỗi production**: chỉ thấy và xác nhận RCA lỗi của dự án trong phạm vi quyền; tên người ngoài phạm vi bị ẩn; tỉ lệ trách nhiệm mỗi người trong (0, 100%], không trùng người. Bỏ label prod-bug: lỗi chưa xác nhận bị xoá, đã xác nhận chuyển `withdrawn` (không trừ điểm).
- **Công thức**: rework 21 ngày trừ xoá theo FIFO (một lần xoá không trừ hai lần thêm; MR `type::refactor` không tính); B2 không có dữ liệu khi tháng không có việc; C3 không đòi pipeline xanh ở repo không có CI; C2 so ngày đóng theo `REPORT_TZ`; C1 tính thời gian viết tới lúc MR "ready" khi draft được mở trước khi có code.
- **Collector**: 401/403 ở test report không làm hỏng sync; test report chỉ hỏi một lần mỗi pipeline; closed_by chỉ hỏi cho issue đóng trong cửa sổ; liên kết issue↔MR không bị hạ hạng và được làm mới theo mô tả MR.

## 12. Phần còn lại của plan (người dùng 2026-11-04: "làm hết đi")
- **AI nháp nhận xét tháng** đọc scorecard DPI của tháng (bản chốt, không có thì bản tạm tính): các chỉ số tự động kèm chi tiết, trích dẫn `(ref: dpi:<mã>)`; **không** đưa tổng điểm, xếp loại hay điểm lead chấm vào prompt.
- **B5 gợi ý MR mẫu**: MR của tháng xếp theo số finding critical/high, rồi kích thước, rồi điểm review thấp; mỗi gợi ý ghi lý do.
- **Duyệt từng mục tự khai** (`evidence_items.approved`): lead duyệt / không tính từng mục; mục chưa quyết định vẫn được tính khi cả hồ sơ đã duyệt (như trước), mục bị "không tính" thì không bao giờ tính.
- **Phân loại đóng góp** (P1.4, hiển thị, không chấm): dòng thêm trong cửa sổ rework chia thành việc mới / refactor (commit `refactor:` hoặc MR `type::refactor`) / sửa code người khác vừa viết (người khác commit cùng file trong 21 ngày trước) và churn (tự xoá lại trong 21 ngày).
- **R1** lọc thêm theo dự án và level. **[Quyết định]** Không có "phòng ban": hệ thống chưa có khái niệm này; team cha (cây team) đóng vai trò đó.
- **R3 Chất lượng & nợ** (`GET /dpi/quality`): lỗi mới trong tháng và lỗi cũ còn mở theo mức; coverage trên MR của tháng so với tháng trước; hotspot (số lần sửa × số finding trong 90 ngày); nợ theo module (finding mở, trọng số 8/4/2/1).
- **R4 Delivery** (`GET /dpi/delivery`): MR đang mở (WIP), issue mở, quá hạn, tuổi backlog (≤7 / 8–30 / 31–90 / >90 ngày), blocker theo nguyên nhân (label `blocked::<lý do>`), theo repo. DORA và các chặng luồng ở Luồng làm việc.
- R3/R4 là số tổng hợp theo repo trong phạm vi dự án người xem thấy; quyền: `dpi.distribution`, `dpi.final_view`, hoặc `dpi.view` từ team trở lên.
- **R6**: 5 trục năng lực (mức đạt TB 3 tháng có dữ liệu, xu hướng 6 tháng) và **sẵn sàng thăng tiến**: rolling ≥ 85 trên ≥ 2 tháng đã chốt và không trục nào < 70% → sẵn sàng; rolling ≥ 80 → đang tiến tới; còn lại chưa. Chỉ là gợi ý cho 1:1.
- **Sửa rubric trên giao diện** (Cấu hình): sửa tên và điểm tối đa từng chỉ số, tạo phiên bản mới; mã, nhóm, người chấm giữ nguyên (công thức gắn với mã).
- **P0 trên GitLab** (Sẵn sàng dữ liệu, quyền `admin.projects`, có hộp xác nhận): **Bảo vệ nhánh chính** (không ai push, Maintainer merge) và **Thêm mẫu MR** (commit `.gitlab/merge_request_templates/Default.md`). Approval rule (≥ 1 approve) là tính năng GitLab Premium: bật tay trong Settings › Merge requests.

## 13. AI gợi ý 30 điểm của lead (người dùng 2026-11-04)
> "Nếu để lead tự mò và chấm điểm thì hơi khó và thiếu khách quan; LLM có thể đề xuất kèm gợi ý giá trị điểm số dựa trên các commit hoặc MR không?"
- Nút **AI gợi ý điểm** ở bước 2 (người có quyền chấm người đó). Job nền đọc bằng chứng của tháng và đề xuất cho **từng chỉ số lead chấm** (A3, B5, D3, E1, E2, E3): điểm (0..max, bước 0,5), độ tin cậy, lý do 1–3 câu, **bằng chứng trích dẫn** (MR, issue, comment, mục tự khai). Không đủ bằng chứng thì không cho điểm và nói thiếu gì.
- Dữ liệu gửi: tiêu đề, kích thước, nhãn, điểm và kết luận review, số finding theo mức/loại của MR đã merge; issue đã đóng (loại, độ phức tạp, đúng hạn, lỗi production); tối đa 12 comment review của người đó (cắt ngắn, che bí mật); mục tự khai và trạng thái duyệt; phản hồi đồng nghiệp (ẩn danh); các chỉ số tự động; xu hướng 5 nhóm 6 tháng. **Không gửi code.** Repo không cho gửi ra LLM ngoài bị bỏ hẳn.
- **[Quyết định]** Chỉ là gợi ý: không bao giờ tự lưu. Lead bấm "Dùng gợi ý" (từng chỉ số hoặc tất cả) để điền sẵn điểm, nhận xét, bằng chứng vào form, sửa rồi Lưu như thường. Người được đánh giá không thấy gợi ý.
- Kết quả bị kiểm: chỉ nhận chỉ số quản lý của rubric của kỳ, điểm bị kẹp trong 0..max, bằng chứng chỉ giữ mã đã đưa vào prompt. Chi phí ghi audit `dpi.suggest.cost`, tính vào ngân sách LLM tháng; hết ngân sách thì không cho chạy.

