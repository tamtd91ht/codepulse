# 23 – Báo cáo thành viên và export

> Ngoài spec gốc, theo yêu cầu người dùng (2026-11-04): "trong menu thành viên có một menu báo cáo và export, lọc theo thời gian, dự án và repo, gồm báo cáo tổng quan và báo cáo theo tiêu chí; UI/UX do agent đề xuất, nhưng ở góc độ người xem muốn biết và đánh giá được chất lượng code của member". Các mục **[Quyết định]** dưới đây do agent chọn theo uỷ quyền của người dùng.

## 1. Người xem cần gì
Lead / quản lý xem báo cáo để trả lời ba câu, theo thứ tự:
1. **Người này viết code tốt tới đâu?** Một mức đánh giá, kèm lý do bằng số.
2. **So với ai?** So với chính họ ở kỳ trước, và với mặt bằng những người cùng bộ lọc.
3. **Bằng chứng ở đâu?** Xu hướng, mức lỗi, loại lỗi hay gặp, repo, và các review cụ thể để mở ra xem.

## 2. Vị trí và bộ lọc
- Trang **Báo cáo thành viên** (`/members/report`), mục riêng trong menu, ngay sau **Thành viên**, cho ai thấy trang Thành viên (`members_page`). Lối vào khác: nút **Báo cáo & export** ở trang Thành viên (mang theo khoảng thời gian) và ở trang từng người (mở sẵn người đó; ai cũng mở được báo cáo của chính mình).
- Bộ lọc ở đầu trang: **thành viên** (người mình xem được, `GET /people`), **dự án**, **repo** (lọc theo dự án đã chọn), **thời gian** (7 / 30 / 90 ngày hoặc tuỳ chọn, tối đa 366 ngày). Mọi bộ lọc nằm trên URL (`?user=&bp=&repo=&range=|from=&to=&tab=`) nên link báo cáo gửi được cho người khác.
- **[Quyết định]** Ranh giới đúng ngày theo `REPORT_TZ`, không làm tròn theo tuần (khác thẻ "Đánh giá trong kỳ"), để số trong báo cáo khớp với khoảng người xem chọn và với file export.

## 3. Tab Tổng quan
1. **Đánh giá chung** (thẻ đầu, viền theo màu mức):
   - **[Quyết định]** Ba mức, cùng ngưỡng dashboard đang dùng:
     - **Cần hỗ trợ**: có critical, hoặc điểm TB < 65 (`needs_attention`);
     - **Tốt**: điểm TB ≥ 80 và tỉ lệ APPROVE ≥ 60% (hoặc chưa có kết luận);
     - còn lại **Cần cải thiện**.
     - Dưới 3 review thì vẫn hiện mức nhưng gắn "ít dữ liệu, chỉ tham khảo".
   - Lý do (do máy chủ viết, dùng chung với file Excel): điểm TB và chênh lệch với mặt bằng và kỳ trước; tỉ lệ APPROVE so với mặt bằng; số critical / high; mật độ lỗi trên 100 dòng so với mặt bằng (lệch quá ±20% thì ghi cao hơn / thấp hơn).
   - Cách xếp mức được ghi ngay dưới thẻ để người xem tự kiểm.
2. **Dải chỉ số** (có mũi tên so với kỳ trước): review (MR / commit), điểm TB, tỉ lệ APPROVE, critical + high, lỗi / 100 LOC, dòng code; dòng phụ ghi mặt bằng.
3. **Điểm theo tuần**: cột số review, đường điểm TB của người đó và đường nét đứt của mặt bằng.
4. **Kết luận và mức lỗi**: tỉ lệ APPROVE / FIX REQUIRED / BLOCK…, finding tính điểm theo mức, khiếu nại được chấp nhận, finding bị kiểm chứng bác bỏ.
5. **Loại lỗi hay gặp**: tỉ trọng mỗi loại trong lỗi của người đó, vạch đánh dấu tỉ trọng của mặt bằng; nhãn "nhiều hơn mặt bằng" khi tỉ trọng > 1,5 lần mặt bằng và có ít nhất 2 finding. Đây là chỗ để biết cần kèm gì.
6. **Theo repo**: review, điểm TB, APPROVE, critical / high.
7. **Review cần xem**: hai danh sách, mỗi danh sách tối đa 8 mục, bấm vào là mở trang review: review có critical / high, và review điểm thấp nhất.

- **Mặt bằng** = mọi review đã xong của những người đang hoạt động mà **người xem** được thấy (`review_scope`), cùng thời gian, dự án và repo; tính cả chính người được xem. Số người của mặt bằng ghi ở dòng phạm vi.
- **Kỳ trước** = khoảng cùng độ dài, liền trước.

## 4. Tab Theo tiêu chí
- Chọn bộ tiêu chí trong số các bộ có điểm cho review của người đó trong kỳ. Bộ chính của dự án đứng trước; khi không lọc dự án thì "bộ chính" là bộ chính của bất kỳ dự án nào có repo người đó làm.
- Đầu thẻ là điểm cuối trung bình (theo LOC) và chênh lệch với mặt bằng. **Điểm mạnh** là tối đa 2 thành phần ≥ 80 và cao hơn mặt bằng nhiều nhất; **cần cải thiện** là tối đa 2 thành phần < 80 và thấp hơn mặt bằng nhiều nhất.
- Bảng thành phần gồm trọng số, thanh điểm (có vạch mặt bằng), điểm, điểm mặt bằng và chênh lệch. Chênh lệch tô xanh khi ≥ 0, vàng khi kém dưới 5 điểm, đỏ khi kém từ 5 điểm.
- Có biểu đồ điểm cuối theo tuần, so với mặt bằng.
- Dữ liệu lấy từ `GET /criteria/summary` (có `user_id` cho người đó, bỏ `user_id` cho mặt bằng) với `set_id`, `from`, `to`, `business_project_id`, `project_id`.

## 5. Export
- **Xuất Excel** (`GET /members/{id}/report.xlsx`, cùng tham số), tên file `codepulse-bao-cao-<email>-<từ>-<đến>.xlsx`, `Cache-Control: no-store`. **[Quyết định]** Excel thay vì CSV, vì một báo cáo có nhiều bảng; thêm phụ thuộc `openpyxl`. Các sheet:
  1. **Tổng quan**: tiêu đề, khoảng, phạm vi, mức đánh giá và lý do; bảng chỉ số gồm các cột kỳ này / kỳ trước / mặt bằng.
  2. **Theo tuần**: số review, điểm TB, điểm TB của mặt bằng.
  3. **Theo repo**.
  4. **Loại lỗi**: số lượng, tỉ trọng, tỉ trọng của mặt bằng.
  5. **Theo tiêu chí**: mỗi bộ có một dòng điểm cuối, rồi mỗi thành phần một dòng (trọng số, điểm, điểm mặt bằng, số finding).
  6. **Review**: mọi review trong kỳ, gồm ngày, repo, loại, `!iid` hoặc sha, tiêu đề, điểm, kết luận, LOC, finding theo mức, điểm từng bộ tiêu chí và link.
- **In / PDF**: in thẳng từ trình duyệt. CSS khi in ẩn menu, bộ lọc và các nút, và không cắt đôi một thẻ.

## 6. Quyền và audit
- Quyền như "Đánh giá trong kỳ": `evaluation.view` phủ người được xem (bản thân, lead, quản lý dự án, admin; `covers_person`). Dự án / repo người xem không thấy → 404; repo không thuộc dự án đã chọn → 422.
- Mọi con số chỉ tính review người xem được thấy (`review_scope`). Mặt bằng cũng vậy, nên không lộ việc của người ngoài phạm vi.
- Audit: `member_report.read` (xem báo cáo người khác), `member_report.export` (mọi lần export).

- Repo không thuộc dự án nào chỉ admin lọc được (như spec 09). Số liệu cần `evaluation.view`; còn **nội dung từng review** (tiêu đề, link, điểm tiêu chí từng review, danh sách "Review cần xem", sheet Review) chỉ gồm review người xem mở được bằng `review.view`.
- File Excel: chuỗi do người dùng nhập (tiêu đề commit / MR, tên repo, tên bộ tiêu chí) luôn ghi dạng chữ, không bao giờ thành công thức Excel (chặn chèn công thức `=...`).
- Có review nhưng không có điểm nào thì xếp **Cần cải thiện**, không xếp Tốt. Người đã tắt (inactive) không có dữ liệu trong báo cáo, như trang Thành viên.
- **[Quyết định, cần theo dõi]** Mặt bằng được tính bằng cách nạp review và finding của cả phạm vi người xem. Ở quy mô hiện tại (vài chục người, tối đa 366 ngày) thì chấp nhận được; nếu chậm thì chuyển sang GROUP BY trong SQL.

## 7. API
- `GET /api/v1/members/{user_id}/report?from&to&business_project_id&project_id` trả JSON gồm: `user`, `from`, `to`, `filters`, `summary`, `previous{from,to,summary}`, `peers` (kèm `members`), `assessment{level,label,reasons,thin}`, `weeks`, `trend`, `trend_reviews`, `peer_trend`, `by_repo`, `categories[{category,label,count,share,peer_share}]`, `lowest_reviews`, `severe_reviews`, `criteria_sets`.
- `GET /api/v1/members/{user_id}/report.xlsx`: cùng tham số.
- Code: `app/api/routers/member_report.py`, `app/scoring/member_report.py`, `dashboards/web/app/members/report/page.tsx`.

## 8. So sánh nhóm (người dùng 2026-11-04: "thêm báo cáo cho cả team, so sánh nhiều thành viên")
- Cùng trang, công tắc **Một người | So sánh nhóm** (`?mode=team`), chỉ hiện cho ai thấy trang Thành viên (`members.view`). Nút **So sánh & export** ở trang Thành viên mở thẳng chế độ này với team và khoảng thời gian đang lọc.
- Chọn ai: **team** (người trong team vào bất kỳ lúc nào trong kỳ), và/hoặc **chọn từng người** (danh sách có ô tìm); bỏ trống cả hai = mọi người mình xem được. Dùng chung bộ lọc thời gian / dự án / repo. Tối đa 100 người.
- **[Quyết định]** Thứ tự đọc: phân bố mức (Cần hỗ trợ / Cần cải thiện / Tốt / Chưa có dữ liệu) → chỉ số cả nhóm so với kỳ trước → ba tab:
  1. **So sánh**: biểu đồ thanh điểm TB từng người (màu theo mức, vạch điểm TB cả nhóm); bảng mỗi người một dòng, **người cần chú ý trước** (mức, rồi điểm thấp), sắp được theo mọi cột: mức (lý do khi rê chuột), review, điểm TB, so với kỳ trước, APPROVE, critical / high, lỗi / 100 LOC, loại lỗi hay mắc nhất. Bấm tên → báo cáo một người với cùng bộ lọc.
  2. **Loại lỗi**: bảng nhiệt người × 8 loại lỗi nhiều nhất của nhóm; ô = tỉ trọng loại đó trong lỗi của người đó, dòng **Cả nhóm** để so. Ô đậm hơn hẳn dòng nhóm = điểm cần kèm riêng.
  3. **Theo tiêu chí**: bảng nhiệt người × thành phần của bộ đã chọn (điểm TB theo LOC; xanh ≥ 80, vàng 65–80, đỏ < 65), điểm cuối, dòng Cả nhóm.
- Mức của từng người tính như §3 nhưng **mặt bằng = cả nhóm đang so sánh** (không phải mọi người trong phạm vi).
- Người có tên trong team / danh sách nhưng không có review vẫn có dòng (mức Chưa có dữ liệu): lead cần biết ai không được review.
- **Export Excel** (`GET /members/team-report.xlsx`): Tổng quan nhóm (chỉ số kỳ này / kỳ trước, số người theo mức), So sánh thành viên (kèm lý do), Loại lỗi (ma trận), Tiêu chí – <bộ> (ma trận), Review (mọi review của nhóm, có cột thành viên). In / PDF như §5.
- Quyền: `members.view`; người được so sánh ∩ `person_scope(evaluation.view)`; team phải `sees_team`; nêu id người ngoài phạm vi → 403. Nội dung review và điểm tiêu chí chỉ từ review mở được bằng `review.view`. Audit `member_report.team_read`, `member_report.team_export`.
- API: `GET /api/v1/members/team-report?from&to&team_id&user_ids=1,2&business_project_id&project_id&set_id` → `summary`, `previous`, `levels`, `weeks`, `trend`, `members[]` (chỉ số, `previous_avg_score`, `assessment`, `trend`, `categories{key: %}`, `top_category`, `criteria{reviews,score,components}`), `categories[]`, `criteria_sets[]`, `criteria{set_id,name,version,components[],group}`. Code: `app/api/routers/team_report.py`, `dashboards/web/components/TeamReport.tsx`.

## 9. Tổng quan / Chi tiết và phân quyền (người dùng 2026-11-04)
> "Báo cáo nên có một báo cáo tổng quan khi vào, cho toàn bộ member, chart ngang theo member, mỗi member một cột / một ô, con số hiện luôn trên chart; lọc member theo dự án; tách 2 tab Tổng quan (nhiều member) và Chi tiết (1 member); hạn chế tối đa cuộn kiểu landing; tổ chức phân quyền, đưa vào bộ quyền." Thay §2 và §8 về cách vào trang.

- **Hai tab đầu trang**: **Tổng quan** (mặc định khi vào) và **Chi tiết**. Link có `?user=` mở thẳng Chi tiết người đó. Người chỉ có phạm vi `own` chỉ thấy Chi tiết của chính mình, không có tab.
- **Thanh lọc gọn một hàng** dưới tab:
  - Tổng quan: dự án, repo, team, người (chọn nhiều), thời gian.
  - Chi tiết: thành viên, dự án, repo, thời gian.
- **Lọc theo dự án** ở Tổng quan liệt kê người có review trong dự án, cộng người được cấp quyền vào dự án (có dòng "Chưa có dữ liệu" nếu chưa có review).
- **Tổng quan**: hàng số người theo mức, rồi **một khung một lúc** (tab con):
  - **Biểu đồ** (mặc định):
    - **Biểu đồ thanh ngang**: mỗi thành viên một thanh, **con số in ngay trên thanh**, người yếu nhất ở trên, màu theo mức, vạch đứt là giá trị cả nhóm.
    - Chọn chỉ số: Điểm TB, APPROVE %, Critical + High, Lỗi / 100 LOC, Số review, Điểm tiêu chí.
    - **Bấm một thanh** để mở Chi tiết người đó.
    - Bên phải: khung **Cả nhóm** (điểm TB so với kỳ trước, APPROVE, review, critical / high, lỗi / 100 LOC) và **Cần chú ý** (người ở mức Cần hỗ trợ / Cần cải thiện, kèm lý do đầu tiên, bấm để mở).
    - Đông người thì biểu đồ và danh sách tự cuộn bên trong khung; trang không dài ra.
  - **Bảng**: bảng so sánh sắp xếp được, tiêu đề cột dính.
  - **Loại lỗi** và **Theo tiêu chí**: các bảng nhiệt như §8.
- **Chi tiết**: tab con **Đánh giá** (mức, lý do, chỉ số, xu hướng, kết luận và mức lỗi), **Loại lỗi & repo**, **Review cần xem**, **Theo tiêu chí**.
- **Sửa lỗi**: bấm tên ở bảng so sánh trước đây không mở được báo cáo riêng (link về cùng trang, trang không đọc lại URL). Nay bấm tên hoặc thanh là chuyển sang tab Chi tiết; trang đọc lại URL khi Back / Forward.
- **Quyền** (spec 21, migration `d2e4f6a8b0c3`):
  - **`report.member`** (own / team / project / all, nhạy cảm): xem báo cáo. Phạm vi quyết định số liệu của ai hiện ở Tổng quan và ai mở được ở Chi tiết.
  - **`report.export`** (flag): xuất Excel. Thiếu quyền này thì không có nút và API trả 403.
  - Bộ có sẵn: Thành viên own, Lead team, Quản lý dự án project, Quản lý tổng all (đều kèm export). Manager không có (Phase 0: manager chỉ xem số tổng hợp của team).
  - Menu "Báo cáo thành viên" hiện cho ai có `report.member`.
  - Thay cho `evaluation.view` / `members.view` ở các endpoint báo cáo.

## 10. Critical và High tách riêng, mỗi loại một cặp số lượng + tỉ lệ (người dùng 2026-11-04)
> "Không nên gộp Critical + High; mỗi loại là một cặp cột: số lượng và tỉ lệ, vì số lượng lớn nhưng số review cũng lớn thì tỉ lệ nhỏ; tỉ lệ khách quan hơn."

- **[Quyết định]** Tỉ lệ = **% review có ít nhất một finding ở mức đó** (được tính điểm như số lượng): số review có critical ÷ số review. Thang 0–100%, dễ đọc ("cứ 100 thay đổi thì bao nhiêu thay đổi có critical"), và một review nhiều lỗi không kéo lệch tỉ lệ. `Bucket` (app/scoring/evaluation.py) có thêm `critical_reviews`, `high_reviews`, `critical_rate`, `high_rate`. Các nơi dùng `Bucket` đều có (báo cáo, Đánh giá trong kỳ, trang Thành viên).
- **Biểu đồ Tổng quan**: thay chỉ số "Critical + High" bằng hai chỉ số **Critical** và **High**. Mỗi người một **cặp thanh**: thanh tỉ lệ (% review có lỗi đó, trục dưới, có màu theo mức, có vạch cả nhóm) và thanh số lượng (trục trên, màu nhạt, nhãn "n lỗi"). Người có tỉ lệ cao nhất ở trên.
- **Bảng so sánh**: bốn cột Critical, % review có critical, High, % review có high; sắp được từng cột.
- **Khung Cả nhóm** và **Chi tiết**: Critical và High là hai ô riêng; ô ghi số lượng (có mũi tên so với kỳ trước), dòng dưới là % review có lỗi đó và mặt bằng. Bảng theo repo cũng tách bốn cột.
- **Excel**: thêm hàng / cột "% review có critical", "% review có high" ở Tổng quan, Theo repo (một người) và Tổng quan nhóm, So sánh thành viên (nhiều người).

