# 07 – Static Analysis bổ trợ (Tuỳ chọn – Phase 4)

AI review có thể bỏ sót hoặc dao động. Kết hợp công cụ tĩnh cho kết quả ổn định, có thể tái lập.

## Lựa chọn
| Công cụ | Cách tích hợp | Map severity |
|---|---|---|
| **Semgrep** (khuyến nghị bắt đầu) | Worker chạy `semgrep --json` trên các file thay đổi | ERROR→high, WARNING→medium, INFO→low; rule bảo mật mức ERROR→critical |
| **SonarQube** (nếu đã có) | Đọc API `/api/issues/search?componentKeys=&createdAfter=` (giống DevLake plugin SonarQube) | BLOCKER→critical, CRITICAL→high, MAJOR→medium, MINOR→low, INFO→info |
| **Gitleaks** | Quét secret trong diff | mọi phát hiện → critical |
| GitLab Code Quality report | Đọc artifact `gl-code-quality-report.json` từ pipeline | theo field `severity` |

## Checklist
- [ ] Interface `StaticAnalyzer.run(project, sha, files) -> list[Finding]`
- [ ] Ghi vào `review_findings` với `source='semgrep'|'sonar'|'gitleaks'`
- [ ] Dedupe với finding AI qua `fingerprint` (cùng file + dòng ± 3 + category)
- [ ] Cấu hình bật/tắt theo project

## Đã làm — phương án C (user 2026-11-12) **[Quyết định]**

Bối cảnh: người dùng muốn “tích hợp SonarLint”. SonarLint là extension của IDE, không có bản chạy
trên server; công ty **chưa có SonarQube**, nên chọn phương án C: công cụ nhẹ chạy ngay trong
worker review. SonarQube (đọc `/api/issues/search`) và artifact Code Quality của GitLab để sau,
khi có (IDEAS.md).

| Câu hỏi | Chọn | Vì sao |
|---|---|---|
| Công cụ | **Gitleaks** (secret lộ) + **Semgrep** (bảo mật, bug) | Miễn phí, chạy offline (Gitleaks) / nhẹ, kết quả ổn định, lặp lại được: bù chỗ AI dao động và hay bỏ sót secret |
| Chạy ở đâu | Worker review, bước `static` sau khi chuẩn bị diff, trước các lượt AI | Kết quả vào được prompt của lượt AI |
| Chạy trên gì | File thay đổi (không phải file xoá, không thuộc IGNORE_PATHS), đọc ở đúng commit qua GitLab API (≤ 60 file, ≤ 1 MB/file); file nhạy cảm (`.env*`) **vẫn được Gitleaks quét** vì nội dung không rời máy | Không cần tải cả repo; secret thường nằm ở file cấu hình |
| Giữ gì | Chỉ vấn đề **nằm trên dòng commit thêm vào** | Không quy lỗi cũ cho người commit |
| Chấm điểm | **Chưa tính** (chạy song song): lưu ở bảng riêng `review_static_findings` (migration `d8f0b2c4e6a9`), không vào điểm, verdict, Critical Board, báo cáo, đóng finding | Đo tỉ lệ báo nhầm trước; tránh trừ điểm hai lần một lỗi |
| Với AI | Danh sách vào prompt (`STATIC ANALYSIS`, ≤ 40 mục, 6 000 ký tự): AI báo finding của chính nó (rule_id = luật của công cụ) khi thật, bỏ qua khi báo nhầm; vấn đề tĩnh trùng dòng (±3) với một finding AI được đánh dấu “AI đồng ý” | AI xác nhận / giải thích, giảm báo nhầm |
| Secret | Gitleaks chạy `--redact`; đoạn code lưu lại qua `mask_code` và che đúng cột của secret: **không secret nào vào DB hay prompt** | |
| Mức độ | Gitleaks → critical/security. Semgrep: ERROR→high, WARNING→medium, INFO→low; security + ERROR + độ tin cậy HIGH → critical; category theo `metadata.category` | Như bảng trên |
| Lỗi | Thiếu công cụ, quá giờ (`STATIC_TIMEOUT_S`, mặc định 120 s mỗi công cụ), lỗi chạy: ghi vào `review.basis.static`, review vẫn tiếp tục | |
| Bật/tắt | Toàn hệ thống: `STATIC_ANALYSIS` (mặc định bật, chỉ chạy khi công cụ có trên worker). Theo repo: cờ **Tĩnh** ở Admin › Dự án & Repo (`projects.static_analysis`, mặc định bật) | |
| Luật Semgrep | `SEMGREP_CONFIG`, mặc định `p/default` (registry, worker cần ra được semgrep.dev; `--metrics=off`). Mạng nội bộ kín: trỏ tới thư mục luật cục bộ | |
| Cài đặt | Dockerfile: Semgrep `1.85.0` trong venv riêng `/opt/semgrep`; Gitleaks `8.21.2` từ bản phát hành, **kiểm sha256** theo file checksums; `--build-arg STATIC_TOOLS=` để build không có | |

Giao diện: trang review có thẻ **Kiểm tra tĩnh** (số file đã kiểm, công cụ nào chạy / lỗi gì,
từng vấn đề: mức độ, file:dòng, công cụ, “AI đồng ý”, mở ra xem thông điệp, đoạn code đã che,
luật); tiến độ review có bước “Kiểm tra tĩnh”.

Code: `app/static_analysis/runner.py` (chạy, đọc kết quả, lọc dòng, che, gợi ý cho prompt),
`ReviewService._static_pass` / `_store_static`. Test: `tests/test_static_runner.py`,
`tests/review/test_service.py::test_static_analysis_hints_are_kept_apart_and_never_scored`.

Sau (chưa làm): xem tỉ lệ “AI đồng ý” vài tuần rồi quyết định tính vào điểm (trọng số riêng,
gộp với finding AI qua fingerprint); SonarQube / Code Quality artifact khi có.


### Gia cố sau review độc lập (2026-11-12)

- Một thay đổi không tắt được công cụ cho chính nó: `.gitleaks.toml`, `.gitleaksignore`, `.semgrepignore`, `.semgrep*` không bao giờ được ghi vào thư mục quét; một `.semgrepignore` rỗng của CodePulse (Semgrep không bỏ qua `tests/`, `vendor/`… như mặc định) và `--gitleaks-ignore-path` trỏ tới file rỗng.
- Một hạn chót cho cả bước (đọc file ≤ ½ STATIC_TIMEOUT_S, cả bước ≤ 2 × STATIC_TIMEOUT_S, đã cộng vào thời gian tối đa của job review); hết giờ thì dừng cả nhóm tiến trình (Semgrep sinh tiến trình con); Semgrep `--jobs 2 --max-memory 2000`; phiên bản công cụ hỏi một lần mỗi tiến trình.
- Bộ luật Semgrep tải một lần lúc build vào image (`SEMGREP_CONFIG=/opt/semgrep/rules/default.yml`): review không cần mạng tới semgrep.dev.
- Thông điệp công cụ cũng được che (không chỉ đoạn code); file nhạy cảm (`.env*`) vẫn được quét nhưng không lưu dòng nào; stderr của công cụ chỉ vào log debug, basis chỉ ghi mã lỗi.
- Gitleaks kiểm theo sha256 ghim sẵn cho amd64 / arm64 (không chỉ file checksum đi kèm).
- Đã chạy với công cụ thật trong image (`tests/test_static_tools.py`, tự bỏ qua khi máy không cài): số dòng khớp, `tests/` được quét, file cấu hình độc hại bị bỏ qua, secret không lộ.
