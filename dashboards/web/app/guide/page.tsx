import type { Metadata } from "next";
import Link from "next/link";

import { GuideNav } from "@/components/GuideNav";

export const metadata: Metadata = { title: "Hướng dẫn" };

const SECTIONS = [
  { id: "intro", label: "CodePulse đo gì" },
  { id: "access", label: "Ai xem được gì" },
  { id: "developer", label: "Developer" },
  { id: "lead", label: "Lead" },
  { id: "admin", label: "Admin" },
  { id: "telegram", label: "Bot Telegram" },
  { id: "mcp", label: "Tích hợp MCP" },
  { id: "metrics", label: "Đọc các chỉ số" },
  { id: "insights", label: "Luồng làm việc" },
  { id: "dpi", label: "DPI hàng tháng" },
  { id: "testcase", label: "Testcase: vai trò, duyệt, hàng loạt" },
  { id: "testcase-more", label: "Testcase: BDD, khám phá, baseline, nhật ký, tham số" },
];

/** §15.9 G7: the five sample permission sets of Testcase and what each holds. */
const TEST_ROLES: [role: string, view: boolean, edit: boolean, del: boolean, approve: boolean, run: boolean, manage: boolean, note: string][] = [
  ["Admin dự án", true, true, true, true, true, true, "Mọi quyền testcase của dự án"],
  ["Lead", true, true, true, true, true, true, "Như admin dự án; thường là người duyệt"],
  ["Tester", true, true, false, false, true, false, "Viết, sửa testcase và chạy test; gửi duyệt"],
  ["Developer", true, false, false, false, true, false, "Ghi kết quả test được giao, tạo bug"],
  ["Guest", true, false, false, false, false, false, "Chỉ xem"],
];
const TEST_PERMS: [key: string, label: string][] = [
  ["tests.view", "Xem"],
  ["tests.edit", "Sửa"],
  ["tests.delete", "Xoá"],
  ["tests.approve", "Duyệt"],
  ["tests.run", "Chạy test"],
  ["tests.manage", "Quản lý"],
];

/** The dashboard copy of docs/USER_GUIDE.md, plus the Admin screen; readable without signing in. */
export default function GuidePage() {
  return (
    <>
      <div className="page-head">
        <div className="titles">
          <div>
            <div className="eyebrow">Guide</div>
            <h1>Hướng dẫn sử dụng</h1>
            <div className="sub">CodePulse review code bằng AI và tổng hợp chất lượng code theo team và từng thành viên.</div>
          </div>
        </div>
      </div>
      <GuideNav />
      <div className="guide">
        <nav className="card toc" aria-label="Mục lục">
          <span className="toc-title">Mục lục</span>
          {SECTIONS.map((s, i) => (
            <a key={s.id} href={`#${s.id}`}>
              {i + 1}. {s.label}
            </a>
          ))}
        </nav>
        <div className="guide-body">

      <section id="intro" className="card">
        <h2>1. CodePulse đo gì và dùng vào việc gì</h2>
        <h3>Đo gì</h3>
        <ul>
          <li>
            Mỗi merge request, và mỗi commit đẩy thẳng lên nhánh chính, được AI review theo một rubric cố định: bảo
            mật, lỗi logic, hiệu năng, khả năng bảo trì, kiểm thử, convention, tài liệu.
          </li>
          <li>
            Mỗi vấn đề (finding) có một mức độ: <span className="sev critical">critical</span>{" "}
            <span className="sev high">high</span> <span className="sev medium">medium</span>{" "}
            <span className="sev low">low</span> <span className="sev info">info</span>
          </li>
          <li>
            Điểm review (0–100) tính bằng <strong>công thức cố định</strong> từ các finding và kích thước thay đổi,
            không phải điểm do AI tự cho. Có ít nhất một lỗi critical thì điểm tối đa là 50; từ rubric v3, có lỗi high
            thì tối đa 80, và MR lớn không còn được “chia đều” lỗi theo tỉ lệ thuận với số dòng.
          </li>
          <li>
            Kèm các chỉ số quy trình: thời gian chờ review, kích thước MR, số vòng review, tỉ lệ build thành công,
            tỉ lệ lỗi nghiêm trọng được sửa ở lần review sau.
          </li>
        </ul>
        <h3>Gửi gì ra ngoài</h3>
        <ul>
          <li>
            Diff của thay đổi được gửi tới Anthropic (Claude) để review. Secret trong code được che trước khi gửi;
            file nhạy cảm (<code>.env*</code>, <code>*.pem</code>, <code>**/secrets/**</code>…) không bao giờ được
            gửi. Repo không cho phép gửi code ra ngoài sẽ không được review.
          </li>
          <li>CodePulse chỉ lưu tối đa 40 dòng trích đoạn quanh mỗi finding, và xoá trích đoạn sau 365 ngày.</li>
        </ul>
        <h3>Dùng vào việc gì</h3>
        <ul>
          <li>
            Là <strong>tín hiệu coaching</strong>: giúp lead thấy chỗ cần hỗ trợ, giúp team chọn chủ đề training.
          </li>
          <li>
            <strong>Không</strong> là căn cứ duy nhất để đánh giá hiệu suất, không tự động xếp hạng lương thưởng.
          </li>
        </ul>
      </section>

      <section id="access" className="card">
        <h2>2. Ai xem được gì</h2>
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Bạn là</th>
                <th>Bạn xem được</th>
              </tr>
            </thead>
            <tbody>
              <tr>
                <td>Developer</td>
                <td>Toàn bộ dữ liệu của chính bạn; số liệu tổng hợp (ẩn danh) của team</td>
              </tr>
              <tr>
                <td>Lead</td>
                <td>Dữ liệu từng thành viên trong team mình</td>
              </tr>
              <tr>
                <td>Manager</td>
                <td>Số liệu tổng hợp của các team trực thuộc, không xem điểm từng người</td>
              </tr>
              <tr>
                <td>Admin</td>
                <td>Cấu hình hệ thống</td>
              </tr>
            </tbody>
          </table>
        </div>
        <p className="muted">Mọi lần xem dữ liệu cá nhân của người khác đều được ghi vào nhật ký kiểm tra (audit log).</p>
        <p>
          <strong>Khi AI sai:</strong> bạn khiếu nại finding đó và lead của bạn duyệt. Nếu được chấp nhận, finding
          không còn tính vào điểm và mọi chỉ số liên quan được tính lại ngay.
        </p>
      </section>

      <section id="developer" className="card">
        <h2>3. Developer</h2>
        <ol>
          <li>
            Bấm <strong>Đăng nhập với GitLab</strong> và dùng tài khoản GitLab của công ty.
          </li>
          <li>
            Mở <strong>Của tôi</strong>: điểm và Quality Index 12 tuần, loại lỗi theo tuần, MR/commit gần đây,
            finding đang mở.
          </li>
          <li>
            Bấm vào một review để xem từng finding kèm trích đoạn code và gợi ý sửa.
          </li>
          <li>
            Thấy finding sai: bấm <strong>Khiếu nại</strong> trên finding đó, ghi lý do và gửi. Theo dõi kết quả ở
            trang <strong>Khiếu nại</strong>.
          </li>
          <li>
            <strong>Chuông thông báo</strong> (góc trên bên phải, cạnh nút đổi giao diện sáng/tối) báo ngay khi review của
            bạn xong, finding được đóng, khiếu nại của bạn được xử lý, và với lead / admin: khi có khiếu nại mới cần
            duyệt. Thông báo mới hiện thêm một toast ở góc trên phải; lịch sử 180 ngày ở trang{" "}
            <Link href="/notifications">Thông báo</Link>.
          </li>
        </ol>
        <p className="muted">
          Nếu repo bật tính năng này, CodePulse đăng một comment tổng hợp lên MR và cập nhật mỗi lần bạn push. Mỗi
          thứ Hai bạn nhận email báo cáo cá nhân, chỉ có dữ liệu của bạn.
        </p>
      </section>

      <section id="lead" className="card">
        <h2>4. Lead</h2>
        <ul>
          <li>
            <strong>Tổng quan</strong>: mọi dự án trong một màn hình: số review, điểm trung bình, critical/high mới và
            đang mở, so với kỳ trước; bấm vào một dự án để xem theo repo, theo thời gian và loại lỗi.
          </li>
          <li>
            <strong>Team</strong>: chỉ số tuần/tháng so với kỳ trước, finding theo mức độ, loại lỗi và file
            &ldquo;nóng&rdquo;, bảng thành viên với xu hướng 8 tuần. Quality Index chỉ hiện khi một người có từ 3
            review trong kỳ.
          </li>
          <li>
            <strong>Critical Board</strong>: mọi finding critical/high còn mở, lọc theo repo hoặc người, cũ nhất
            trước.
          </li>
          <li>
            <strong>Báo cáo thành viên</strong> có hai tab. <strong>Tổng quan</strong> (mở sẵn): mỗi thành viên một thanh
            ngang, số in ngay trên thanh, người yếu nhất ở trên; đổi chỉ số (điểm TB, APPROVE, critical/high, lỗi/100 dòng,
            số review, điểm tiêu chí); lọc theo dự án, repo, team hoặc chọn người; bên phải là số cả nhóm và danh sách
            cần chú ý; tab con Bảng / Loại lỗi / Theo tiêu chí. Bấm một thanh hoặc tên để mở <strong>Chi tiết</strong> người
            đó: mức đánh giá kèm lý do, loại lỗi và repo, review cần xem, điểm theo tiêu chí. <strong>Xuất Excel</strong> và{" "}
            <strong>In / PDF</strong> theo tab đang xem. Quyền: &ldquo;Xem báo cáo thành viên&rdquo; (theo phạm vi) và
            &ldquo;Xuất Excel báo cáo thành viên&rdquo; trong Admin › Phân quyền.
          </li>
          <li>
            <strong>Duyệt khiếu nại</strong>: chấp nhận hoặc từ chối. Bạn không thể tự duyệt khiếu nại của mình.
          </li>
          <li>Review bị lỗi hoặc bị bỏ qua có thể chạy lại ngay từ trang review.</li>
          <li>
            Báo cáo tuần (thứ Hai 08:00) và tháng (ngày 1) gửi qua email; kênh chat của team nhận bản không có điểm
            từng người.
          </li>
        </ul>
      </section>

      <section id="admin" className="card">
        <h2>5. Admin</h2>
        <p className="muted">
          Tài khoản admin là các email trong <code>ADMIN_EMAILS</code>. Mọi thay đổi ở trang Admin đều được ghi audit
          log.
        </p>
        <h3>Kết nối GitLab</h3>
        <ol>
          <li>
            Trên GitLab, tạo một tài khoản bot (ví dụ <code>codepulse-bot</code>) và thêm vào các group/repo cần đo với
            quyền <em>Reporter</em>.
          </li>
          <li>
            Tạo Personal Access Token cho bot với scope <code>read_api</code>. Thêm scope <code>api</code> chỉ khi muốn
            CodePulse comment lên MR.
          </li>
          <li>
            Vào <strong>Admin → Kết nối GitLab</strong>, nhập tên, URL GitLab và token, rồi bấm{" "}
            <strong>Thêm kết nối</strong>. Token được mã hoá trong cơ sở dữ liệu và không bao giờ hiển thị lại.
          </li>
          <li>
            Đổi token: nhập token mới ở dòng của kết nối và bấm <strong>Đổi token</strong>. CodePulse thử token với
            GitLab trước khi lưu; sau đó hãy thu hồi token cũ trên GitLab. Biểu tượng bút đổi tên kết nối; chỉ xoá được
            kết nối khi không còn repo nào dùng nó.
          </li>
        </ol>
        <h3>Dự án, group và repo</h3>
        <ul>
          <li>
            <strong>Repo</strong> là một git repo (trên GitLab gọi là project). <strong>Group</strong> là group trên
            GitLab chứa repo. <strong>Dự án</strong> do admin đặt trong CodePulse (ví dụ OmiCRM) và gom nhiều repo, có
            thể từ nhiều group.
          </li>
          <li>
            Tab <strong>Dự án &amp; Repo</strong> mặc định hiện mỗi dự án là một ô (số repo, bao nhiêu repo bật review,
            sync gần nhất); bấm vào ô để mở ngăn chi tiết: đầu ngăn là tên, mô tả và số liệu (repo, bật review, sync,
            bot Telegram, webhook thông báo), bên dưới chia tab <strong>Repo</strong> (đầy đủ nút Sync / Webhook / Review
            / Bỏ theo dõi, thêm repo có sẵn), <strong>Quyền truy cập</strong>, <strong>Tiêu chí chấm</strong>,{" "}
            <strong>Quy tắc review</strong> và <strong>Repo .claude</strong>; tab dùng lần trước được nhớ. Nút{" "}
            <strong>Danh sách</strong> chuyển sang bảng toàn bộ repo để lọc, chọn nhiều và thao tác hàng loạt.
          </li>
          <li>Xoá dự án chỉ bỏ cách nhóm; repo và lịch sử review vẫn giữ.</li>
        </ul>
        <h3>Chấm lại điểm theo tiêu chí</h3>
        <p>
          Admin → Dự án &amp; Repo → bấm dự án → tab <strong>Tiêu chí chấm</strong> → <strong>Chấm lại điểm…</strong>. Chọn bộ
          tiêu chí, repo, người và khoảng thời gian, bấm <strong>Xem trước</strong> để biết bao nhiêu review sẽ đổi điểm, bao
          nhiêu finding chưa được xếp vào thành phần và bao nhiêu kết quả quá sơ sài; đọc cảnh báo, tick xác nhận rồi bấm{" "}
          <strong>Chấm lại</strong>. Điểm được tính lại từ kết quả review đã có, không review lại code. Tuỳ chọn: để Claude xếp
          các finding chưa được xếp (tốn một ít ngân sách), và review lại thật các kết quả quá sơ sài (tốn như review mới).
        </p>
        <h3>Quy tắc review theo dự án</h3>
        <p>
          Trong ngăn chi tiết của một dự án (Admin → Dự án &amp; Repo → bấm ô dự án) có tab <strong>Quy tắc review</strong>:
          markdown mô tả các quy tắc của team (tenant isolation, money safety, PII, convention…). Mỗi repo có thể thêm
          quy tắc riêng và chỉ tới file quy tắc nằm trong repo. Reviewer đọc các quy tắc này cùng diff; finding vi phạm
          ghi rõ tên quy tắc. Từ rubric v2, mỗi review còn có <strong>kết luận merge</strong> (APPROVE / FIX REQUIRED /
          BLOCK, risk = critical×10 + high×3 + medium×1) và nhận xét <em>thay đổi làm gì, có đúng hướng không</em>.
        </p>
        <h3>Review sâu</h3>
        <p>
          Mặc định mỗi review có hai lượt. Lượt nhanh chỉ đọc diff. Lượt sâu tải bản sao repo tại đúng commit, đọc
          README, <code>CLAUDE.md</code>, <code>AGENTS.md</code>, <code>.claude/rules</code>, tài liệu bảo mật và
          manifest để dựng <strong>hồ sơ dự án</strong> (dự án là gì, stack, mô hình bảo mật, vùng rủi ro; xem ở trang
          dự án), rồi reviewer mở mã nguồn (chỉ đọc) để đối chứng từng finding: giữ, hạ mức hay loại bỏ kèm file bằng
          chứng, và tìm lỗi chỉ thấy được khi nhìn nhiều file. Tắt theo repo bằng cờ <strong>Sâu</strong>. Hồ sơ cũ thì
          bấm <strong>Làm mới hồ sơ</strong> trong ngăn chi tiết dự án.
        </p>
        <h3>Claude review như thế nào (và đọc những skill nào)</h3>
        <ul>
          <li>
            CodePulse gọi <strong>Claude Code CLI</strong> ở chế độ không tương tác, bằng phiên của gói Claude đã đăng
            nhập ở Admin (hoặc token trong .env). CLI chạy với <code>--setting-sources &quot;&quot;</code>: nó{" "}
            <strong>không</strong> tự nạp <code>CLAUDE.md</code>, skills hay settings của repo như khi bạn dùng Claude
            Code trên máy. Thay vào đó CodePulse tự chọn file và chép nội dung vào prompt dưới dạng dữ liệu tham khảo, để
            nội dung trong repo không thể ra lệnh cho reviewer.
          </li>
          <li>
            <strong>Lượt nhanh</strong>: chỉ diff + quy tắc dự án/repo (ô Quy tắc review và các file quy tắc đã khai báo).
            Không có tool, không đọc <code>.claude</code>.
          </li>
          <li>
            <strong>Lượt sâu</strong> làm như một người review của dự án: đọc <strong>quy trình review</strong> trước
            (các lệnh có tên chứa <em>review</em> trong <code>.claude/commands</code>, ví dụ <code>/review-mr-dev</code>
            , lệnh review MR/commit đứng đầu), rồi nạp đúng những skill / rule / agent mà thay đổi cần. CodePulse trích
            sẵn <code>CLAUDE.md</code>, file ở <strong>File nạp thêm</strong> và vài file khớp nhất với commit (rule có{" "}
            <code>paths:</code> khớp file bị sửa, file mà lệnh review nhắc tên, file có từ khoá trùng với đường dẫn /
            mã / tiêu đề thay đổi). Các file còn lại nằm trong một mục lục kèm mô tả; repo .claude dùng chung được chép
            vào bản sao nên agent tự mở (Read) file nào cần, như cách Claude Code nạp skill khi dùng. Áp dụng cho cả{" "}
            <code>.claude</code> của repo được review và repo dùng chung của dự án. Reviewer chỉ có Read/Grep/Glob, tối
            đa 60 lượt.
          </li>
          <li>
            Thẻ <strong>Căn cứ review</strong> trên trang kết quả liệt kê quy trình review đã dùng, từng file được trích
            kèm lý do chọn, các file agent đã tự mở thêm, và phần mục lục không cần đến.
          </li>
        </ul>
        <h3>Repo .claude dùng chung của dự án</h3>
        <p>
          Nếu các service của dự án dùng chung một repo cấu hình Claude (skills, agents, rules, CLAUDE.md), mở ngăn chi
          tiết dự án → <strong>Repo .claude dùng chung</strong>, nhập đường dẫn GitLab (ví dụ{" "}
          <code>omicrm/claude-config</code>), nhánh nếu không dùng nhánh mặc định, và các file cần nạp thêm. Mục này
          không bắt buộc. Repo có thể chứa thư mục <code>.claude/</code> hoặc chính là nội dung thư mục đó. Nút{" "}
          <strong>Kiểm tra</strong> tải repo và cho biết file nào sẽ được nạp. Khi một file có cả ở repo dùng chung và ở
          repo đang review, bản của repo đang review được ưu tiên. Repo dùng chung phải nằm cùng kết nối GitLab với các
          repo của dự án.
        </p>
        <h3>Webhook thông báo</h3>
        <p>
          Ở tab <strong>Webhook thông báo</strong>, admin đặt endpoint nhận kết quả review (Slack, Teams hoặc service nội
          bộ nhận JSON có chữ ký HMAC), theo phạm vi mọi dự án / một dự án / một repo và theo sự kiện: review xong, có
          critical/high, review lỗi. Khác với webhook GitLab (GitLab gọi CodePulse khi có MR mới), webhook này là chiều
          CodePulse gọi ra ngoài. Nút <strong>Gửi thử</strong> gửi payload mẫu để kiểm tra endpoint.
        </p>
        <h3>Tiến trình review</h3>
        <p>
          Khi có review đang chạy, góc phải dưới hiện nút <strong>Đang review …</strong>. Bấm vào để xem từng review: đang
          ở bước nào (lấy diff, review AI, review sâu, chấm điểm), đã chạy bao lâu, ai yêu cầu, và các review đang chờ.
          Review vừa xong có link tới trang kết quả. Bạn chỉ thấy chi tiết review của mình và người mình lead; còn lại chỉ
          hiện số lượng. Ở bước review sâu có nút <strong>Xem trực tiếp</strong>: danh sách Claude đang mở file nào, tìm
          gì (Read / Grep / Glob), đang ở lượt mấy. Chỉ hiện tên tool, đường dẫn và mẫu tìm kiếm; nội dung file và lời
          của model không được gửi lên trình duyệt. Diễn biến được giữ một giờ.
        </p>
        <h3>Nhánh review của repo</h3>
        <p>
          Mặc định, danh sách &quot;commit gần đây&quot; lấy từ nhánh mặc định của GitLab. Nếu team làm trên{" "}
          <code>develop</code> hay nhánh khác, vào Admin › Repo, bấm vào ô <strong>Nhánh</strong> của repo và chọn nhánh
          (hoặc <strong>Mọi nhánh</strong>). Telegram và trang Yêu cầu review sẽ dùng nhánh này. Trên Telegram có thể gõ{" "}
          <code>branch=all</code> để xem mọi nhánh một lần.
        </p>
        <h3>Lịch sử review và review lại</h3>
        <p>
          Mỗi lần review là một bản ghi riêng theo commit hoặc theo head của MR; không bao giờ bị xoá và không review lại
          tự động. Lead/admin có thể bấm <strong>Review lại</strong> trên trang review của một commit (bất kỳ) hoặc của MR
          còn mở ở đúng head hiện tại: lần mới thay lần cũ trong điểm tuần/tháng, lần cũ vẫn xem được trong bảng{" "}
          <strong>Lịch sử review</strong>. Trên Telegram, lead/admin bấm <strong>↻ Cho chọn mục đã review</strong> trong danh sách chọn, hoặc thêm{" "}
          <code>again=1</code> vào lệnh <code>/review</code> (mặc định không review lại). Lần cũ chỉ bị
          thay khi lần mới review xong.
        </p>
        <h3>Thêm repo</h3>
        <ol>
          <li>Lấy Project ID của repo trên GitLab: ngay dưới tên repo, hoặc Settings → General.</li>
          <li>
            Ở thẻ <strong>Repo</strong>, chọn kết nối, nhập Project ID, chọn dự án (tuỳ chọn), giữ tick{" "}
            <strong>Đăng ký webhook</strong> rồi bấm <strong>Thêm repo</strong>.
          </li>
          <li>
            Thêm nhiều repo một lần: nhập Group ID hoặc đường dẫn group (ví dụ <code>vihat/omicrm</code>), chọn dự án
            rồi bấm <strong>Thêm cả group</strong>. Mọi repo đang hoạt động trong group và subgroup được thêm; repo tạo
            sau này cần thêm lại. Bot phải có quyền Reporter trên group.
          </li>
          <li>
            CodePulse tự lấy dữ liệu 90 ngày gần nhất (<code>BACKFILL_DAYS</code>). Bật tuỳ chọn cho từng repo:{" "}
            <strong>AI review</strong>, <strong>Comment MR</strong>, <strong>Gửi code ra LLM</strong> (thiếu tuỳ chọn
            cuối thì repo không được review).
          </li>
        </ol>
        <h3>Quản lý repo</h3>
        <ul>
          <li>
            Tab <strong>Đang theo dõi / Đã bỏ theo dõi / Tất cả</strong> và bộ lọc theo dự án, group, tên. Repo được
            nhóm theo group; chọn ở dòng group để gán cả group vào một dự án.
          </li>
          <li>
            <strong>Sync</strong>: lấy commit/MR mới ngay (bình thường tự chạy mỗi 30 phút).{" "}
            <strong>Webhook</strong>: đăng ký hoặc làm mới webhook để GitLab báo push/MR mới ngay lập tức.
          </li>
          <li>
            <strong>Review…</strong>: chọn khoảng ngày để review các MR chưa được review (đã merge, và MR đang mở nếu
            tick). MR mới hoặc có commit mới được review tự động qua webhook, không cần bấm.
          </li>
          <li>
            <strong>Bỏ theo dõi</strong>: ngừng sync và review repo không cần quan tâm; lịch sử review vẫn giữ.{" "}
            <strong>Theo dõi lại</strong> ở tab Đã bỏ theo dõi.
          </li>
          <li>
            <strong>Gỡ hẳn</strong> (biểu tượng thùng rác): xoá repo cùng toàn bộ commit, MR, review, finding, khiếu nại và
            gỡ webhook; điểm các kỳ trước được tính lại. Không hoàn tác được, phải dán lại chuỗi xác nhận.
          </li>
          <li>
            Tick ô đầu dòng (hoặc ô ở dòng group để chọn cả group) để gán dự án, bỏ theo dõi, theo dõi lại hoặc gỡ nhiều
            repo cùng lúc. Các nút trong bảng là biểu tượng; rê chuột vào để xem tên.
          </li>
        </ul>
        <h3>Team và người dùng</h3>
        <ol>
          <li>
            Ở thẻ <strong>Team &amp; thành viên</strong>, tạo team (có thể chọn team cha, ví dụ Backend thuộc
            Engineering).
          </li>
          <li>
            Thêm người dùng bằng họ tên và email công ty. Email phải trùng email trên GitLab để commit/MR tự gắn đúng
            người.
          </li>
          <li>
            Bấm <strong>Thành viên</strong> ở dòng của team, chọn người, vai trò (Thành viên, Lead, Manager) và ngày vào
            team rồi bấm <strong>Thêm vào team</strong>. Đổi vai trò ngay trong bảng; <strong>Rời team</strong> ghi ngày
            rời thay vì xoá, để chỉ số các kỳ trước vẫn đúng.
          </li>
          <li>
            Tài khoản Git, GitLab hoặc Telegram chưa nhận ra được hiện ở <strong>Tài khoản chưa gắn người</strong>; chọn
            người tương ứng để gắn. Có nhiều người cùng lúc thì vẫn có thể nhập CSV bằng lệnh <code>codepulse identity import</code>.
            Ô <strong>Chuyển cả các review trước đây của tài khoản…</strong> (bật sẵn) chuyển các review cũ của tài khoản đó sang
            người được gắn và tính lại điểm các tuần/tháng đã chốt ở nền; lần sync tự động cũng làm vậy với các tài khoản
            nó tự nhận ra.
          </li>
          <li>
            Ở <strong>Kết quả review</strong>, dòng <strong>Chưa gắn người</strong> ghi tên và email git (hoặc tài khoản
            GitLab) của tác giả. Admin và người quản lý dự án bấm <strong>Gắn</strong>, chọn người, rồi trả lời{" "}
            <strong>Đồng bộ dữ liệu cũ?</strong>: <strong>Có</strong> chuyển mọi review của tác giả đó sang người này và
            tính lại điểm; <strong>Không</strong> chỉ gắn từ nay, dòng còn nút <strong>Đồng bộ</strong> để làm sau.
          </li>
        </ol>
        <h3>Telegram</h3>
        <p>
          Tab <strong>Telegram</strong>: thêm bot (token từ @BotFather) cho một dự án, hoặc chỉ một repo của dự án; có thể
          giới hạn chat id. Bot được kiểm tra (getMe) khi lưu, token mã hoá trong DB. Hệ thống tự poll vài giây một lần;
          cột trạng thái cho biết lần poll gần nhất và lỗi nếu có.
        </p>
        <h3>Chi phí LLM</h3>
        <p>
          Tab <strong>LLM &amp; Chi phí</strong> cho thấy số review, chi phí ước tính và phần trăm ngân sách tháng, và nút
          kiểm tra kết nối LLM.
        </p>
        <h3>Đăng nhập Claude và cài đặt review</h3>
        <p>
          Thẻ <strong>Đăng nhập Claude</strong> cho review chạy bằng phiên của gói Claude (Pro/Max) thay vì API key:
          bấm <strong>Đăng nhập Claude</strong>, đăng nhập trên claude.ai, dán mã vào ô xác nhận; hoặc dán token tạo bằng{" "}
          <code>claude setup-token</code>. Token được mã hoá và ưu tiên hơn cấu hình trong .env. Thẻ{" "}
          <strong>Cài đặt review</strong> đổi ngưỡng số dòng tối đa mỗi MR/commit (<code>MAX_REVIEW_LOC</code>); diff
          lớn hơn bị bỏ qua với lý do &quot;MR quá lớn&quot;.
        </p>
        <h3>Kết nối LLM</h3>
        <p>
          Ở thẻ <strong>Kết nối LLM</strong>, bấm <strong>Kiểm tra kết nối</strong> để xem provider, model, phiên bản
          Claude Code CLI, cách xác thực (chỉ hiện vài ký tự của token) và tài khoản nếu CLI cho biết, rồi gửi thử một
          prompt rất nhỏ (không có code) để đo trạng thái, độ trễ và chi phí. Mỗi lần kiểm tra tốn một ít quota và được
          ghi audit log.
        </p>
        <h3>Token tích hợp MCP</h3>
        <p>
          Ở <strong>Người &amp; Team → Người dùng</strong>, nút chìa khoá trên mỗi dòng mở danh sách token Claude Code của người
          đó (tên, hạn, lần dùng cuối) để thu hồi khi máy bị mất, người nghỉ việc hoặc token bị lộ. Dashboard chuyển tiếp{" "}
          <code>/mcp</code> tới API giống <code>/api</code>, nên reverse proxy đặt trước dashboard không cần thêm cấu hình
          (miễn là nó chuyển nguyên header <code>Authorization</code>, như mặc định của nginx và Traefik).
        </p>
      </section>

      <section id="telegram" className="card">
        <h2>6. Bot Telegram</h2>
        <p>
          Mỗi dự án có thể có một bot Telegram (admin cấu hình ở <strong>Admin → Telegram</strong>). Bạn gửi lệnh cho bot
          trong chat riêng hoặc nhóm của team; review chạy như bình thường và kết quả (điểm, số finding, link) được trả
          về cùng chat.
        </p>
        <h3>Liên kết tài khoản</h3>
        <ol>
          <li>
            Mở <strong>Của tôi → Telegram → Tạo mã liên kết</strong>.
          </li>
          <li>
            Gửi cho bot: <code>/link MÃ</code> (mã dùng một lần, sống 15 phút). Gõ <code>/whoami</code> để kiểm tra.
          </li>
        </ol>
        <h3>Lệnh</h3>
        <div className="table-wrap">
          <table>
            <tbody>
              <tr>
                <td>
                  <code>/review</code>
                </td>
                <td>
                  Menu nút bấm: <strong>Commit gần đây</strong> hoặc <strong>MR đang mở</strong> → repo → <strong>của ai</strong>{" "}
                  (Mọi người hoặc một người bạn được review) → tick các mục rồi bấm <strong>Review đã chọn</strong>. Không cần mở GitLab lấy số MR hay mã commit. Cùng kết quả với{" "}
                  <code>/review type=commit repo=…</code> không truyền <code>user=</code>.
                </td>
              </tr>
              <tr>
                <td>
                  <code>/review type=mr id=412 repo=global-api</code>
                </td>
                <td>
                  Review merge request !412 (hoặc <code>id=</code>link MR). Repo viết tắt được: <code>global-api</code> hiểu là{" "}
                  <code>cloud-saas-global-api</code>; bot tự chọn, còn mơ hồ thì hỏi lại.
                </td>
              </tr>
              <tr>
                <td>
                  <code>/review type=commit id=a1b2c3d</code>
                </td>
                <td>Review một commit (sha hoặc link)</td>
              </tr>
              <tr>
                <td>
                  <code>/review type=commit user=tamtd</code> · <code>/review type=mr user=an</code>
                </td>
                <td>
                  Commit / MR gần nhất của một người (viết tắt theo username, email hoặc tên; bỏ <code>user=</code> là
                  của bạn; chỉ người quản lý dự án và admin review được người khác)
                </td>
              </tr>
              <tr>
                <td>
                  <code>/review type=commit user=tamtd range=7d</code>
                </td>
                <td>Mọi commit trong 7 ngày qua của người đó chưa được review (<code>range=today</code> cho hôm nay)</td>
              </tr>
              <tr>
                <td className="muted">Dạng cũ</td>
                <td className="muted">
                  <code>/review mr 123 api</code>, <code>/review commit sha</code>, <code>/review last|lastmr|today …</code> vẫn
                  dùng được
                </td>
              </tr>
              <tr>
                <td>
                  <code>/review open</code>
                </td>
                <td>Các MR đang mở chưa được review: của bạn (quản lý dự án / admin: của mọi người)</td>
              </tr>
              <tr>
                <td>
                  <code>/status</code> · <code>/critical</code> · <code>/score</code>
                </td>
                <td>Review gần đây, lỗi nghiêm trọng đang mở, điểm kỳ gần nhất</td>
              </tr>
              <tr>
                <td>
                  <code>/report user=tamtd time=04-2026,10-2026</code>
                </td>
                <td>
                  Báo cáo review theo khoảng thời gian. <code>time</code>: tháng (<code>04-2026</code>), ngày, năm, quý
                  (<code>q2-2026</code>), <code>7d</code>, <code>lastmonth</code>, hoặc hai mốc nối bằng dấu phẩy; bỏ trống = tháng
                  này. <code>user=all</code>: cả team
                </td>
              </tr>
            </tbody>
          </table>
        </div>
        <p className="muted">
          Quyền giống dashboard: thành viên review việc của mình, lead review người trong team mình, admin mọi người. Lệnh
          review tốn ngân sách LLM và được ghi audit log.
        </p>
      </section>

      <section id="mcp" className="card">
        <h2>7. Tích hợp MCP</h2>
        <p>
          CodePulse có một máy chủ MCP để <strong>Claude Code</strong> trên máy bạn đọc các finding{" "}
          <span className="sev critical">critical</span> <span className="sev high">high</span> còn mở của bạn (top, theo
          commit hoặc theo MR), sửa code ngay trong repo rồi <strong>gửi yêu cầu đóng</strong> kèm commit, file đã sửa và kết
          quả test. Yêu cầu đi đúng luồng duyệt như gửi trên web.
        </p>
        <ol>
          <li>
            Tạo token ở <strong>Của tôi → Tích hợp MCP → Tạo token</strong> (chỉ hiện một lần).
          </li>
          <li>
            Chạy lệnh <code>claude mcp add</code> hiện ra, hoặc dùng <code>.mcp.json</code> với biến{" "}
            <code>CODEPULSE_TOKEN</code>.
          </li>
          <li>
            Trong repo, bảo Claude Code lấy finding của bạn, sửa, chạy test; bạn commit và push; Claude gửi yêu cầu đóng.
          </li>
        </ol>
        <p>
          Tài liệu đầy đủ (cài đặt theo hệ điều hành, tham số từng tool, gửi yêu cầu đóng, lệnh mẫu <code>/fix-findings</code>,
          mã lỗi, giới hạn, bảo mật, dành cho admin):{" "}
          <Link href="/guide/mcp">
            <strong>Hướng dẫn › Tích hợp MCP</strong>
          </Link>
          . Báo cáo sử dụng và hiệu quả ở trang <Link href="/mcp-report">Tích hợp MCP</Link> (menu Theo dõi).
        </p>
      </section>

      <section id="metrics" className="card">
        <h2>8. Đọc các chỉ số</h2>
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Chỉ số</th>
                <th>Ý nghĩa</th>
              </tr>
            </thead>
            <tbody>
              <tr>
                <td>Điểm review</td>
                <td>100 trừ phần phạt theo mức độ × độ tin cậy, chia theo kích thước MR (mỗi 200 dòng)</td>
              </tr>
              <tr>
                <td>Điểm TB</td>
                <td>Trung bình có trọng số theo số dòng đã review</td>
              </tr>
              <tr>
                <td>Mật độ lỗi</td>
                <td>Phần phạt trên mỗi 100 dòng thay đổi</td>
              </tr>
              <tr>
                <td>Fix rate</td>
                <td>Tỉ lệ lỗi critical/high biến mất ở lần review sau của cùng MR</td>
              </tr>
              <tr>
                <td>Quality Index</td>
                <td>
                  50% điểm TB + 20% (mật độ lỗi thấp) + 15% build thành công + 15% fix rate; phần không có dữ liệu
                  được bỏ qua
                </td>
              </tr>
            </tbody>
          </table>
        </div>
        <p className="muted">Câu hỏi về cách tính hoặc dữ liệu của bạn: liên hệ admin CodePulse của công ty.</p>
      </section>

      <section id="insights" className="card">
        <h2>9. Luồng làm việc: đo gì và không đo gì</h2>
        <p>
          Menu <strong>Luồng làm việc</strong> cho thấy công việc chảy qua team thế nào. Đây là chỉ số <strong>của dự án/repo</strong>{" "}
          để cải thiện quy trình (đặt SLA review, tách MR nhỏ, giảm phụ thuộc một người), <strong>không</strong> dùng để chấm
          điểm hay xếp hạng ai. Không có điểm tổng cho từng người.
        </p>
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Chỉ số</th>
                <th>Cách tính</th>
              </tr>
            </thead>
            <tbody>
              <tr>
                <td>Viết code</td>
                <td>Commit đầu tiên → mở MR (tác giả)</td>
              </tr>
              <tr>
                <td>Chờ review</td>
                <td>MR sẵn sàng (hết draft) → phản hồi đầu tiên của người khác, comment hoặc approve (reviewer/team)</td>
              </tr>
              <tr>
                <td>Review → merge</td>
                <td>Phản hồi đầu tiên → merge</td>
              </tr>
              <tr>
                <td>Merge không review</td>
                <td>MR merge khi chưa có ai khác phản hồi</td>
              </tr>
              <tr>
                <td>Cỡ MR</td>
                <td>Dòng thay đổi; XL ≥ 1000 dòng là khó review kỹ, không phải lỗi của ai</td>
              </tr>
              <tr>
                <td>Tải review</td>
                <td>Mỗi người review bao nhiêu MR; không đếm số comment, không chấm comment hay dở</td>
              </tr>
              <tr>
                <td>Rủi ro tri thức</td>
                <td>Module chỉ một người nắm (bus factor 1) hoặc người nắm đã vắng: rủi ro của team</td>
              </tr>
            </tbody>
          </table>
        </div>
        <p className="muted">
          Thời gian tính theo lịch (cả cuối tuần), dùng trung vị và p75; ít hơn 5 MR thì không hiện trung vị. Comment tóm tắt của
          CodePulse không tính là review. Tên người chỉ hiện với ai được xem việc của người đó; người khác thấy &quot;Một thành
          viên&quot;.
        </p>
      </section>

      <section id="dpi" className="card">
        <h2>10. DPI: đánh giá hiệu suất hàng tháng</h2>
        <p>
          Mỗi người một scorecard mỗi tháng: <strong>70 điểm chỉ số kỹ thuật tự động</strong> (giao việc, chất lượng code, quy trình,
          review cho người khác) và <strong>30 điểm lead chấm</strong> (tác động, thiết kế, làm việc nhóm, ownership), trọng số theo
          level. Đang <strong>chạy thử</strong>: không dùng cho lương thưởng cho tới khi quản lý tắt chế độ này.
        </p>
        <ol>
          <li>Ngày 1: hệ thống tính scorecard tạm tính tháng trước cho mọi người có hoạt động Git.</li>
          <li>Hạn ngày 3: bạn tự khai trong Hồ sơ tháng (đóng góp tiêu biểu, việc ngoài Git, blocker, ngày nghỉ) rồi bấm Gửi.</li>
          <li>Hạn ngày 7: lead chấm 30 điểm, mỗi ô có nhận xét và bằng chứng; xác nhận RCA lỗi production.</li>
          <li>Hạn ngày 10: người có quyền hiệu chỉnh xem phân phối giữa các team, điều chỉnh ±10 điểm kèm lý do.</li>
          <li>Sau 1:1: lead chốt với action plan tháng sau; tự khoá sau 7 ngày. Trước khi chốt, bạn khiếu nại được từng chỉ số.</li>
        </ol>
        <p>Để chỉ số có dữ liệu, mỗi repo cần:</p>
        <ul>
          <li>
            Nhánh chính có bảo vệ, mọi thay đổi đi qua MR có ít nhất 1 approve (Settings › Repository › Protected branches; Settings ›
            Merge requests › Approvals).
          </li>
          <li>
            Label: <code>complexity::XS|S|M|L|XL</code> (1/2/3/5/8 điểm), <code>type::feature|bug|refactor|chore|docs|incident|automation</code>,{" "}
            <code>prod-bug::P1|P2|P3</code>, <code>blocked</code>. Admin tạo nhanh bằng nút &quot;Tạo label chuẩn&quot; ở DPI › Sẵn sàng dữ liệu.
          </li>
          <li>
            Nếu nguồn task là <strong>Jira</strong> (Admin chọn ở DPI › Jira): độ phức tạp lấy từ <strong>Story Points</strong> thay cho label{" "}
            <code>complexity::</code>, loại lấy từ issue type, lỗi production từ Bug có label <code>production</code> / <code>prod-bug</code> và
            Priority. MR nối issue bằng key trong tiêu đề hoặc tên nhánh (<code>feature/ABC-123-mo-ta</code>). Xem{" "}
            <Link href="/guide/jira">
              <strong>Hướng dẫn › Tích hợp Jira</strong>
            </Link>
            .
          </li>
          <li>
            Mẫu MR <code>.gitlab/merge_request_templates/Default.md</code> có dòng <code>Closes #</code> (với Jira: key <code>ABC-123</code> trong tiêu đề MR) và checklist (đã liên kết issue,
            có test, migration hai chiều, có cách rollback).
          </li>
          <li>
            CI xuất coverage: dòng <code>coverage:</code> với regex của công cụ test, và <code>artifacts:reports:junit</code> /{" "}
            <code>coverage_report</code> (JaCoCo cho Java, <code>go test -coverprofile</code>, Jest/Vitest <code>--coverage</code>).
          </li>
        </ul>
        <p className="muted">
          Chỉ số thiếu dữ liệu không bị tính 0 mà làm giảm độ phủ; độ tin cậy thấp thì không xếp loại. Không có bảng xếp hạng toàn công
          ty; manager chỉ thấy phân phối của team. Chi tiết công thức: mở một chỉ số trên scorecard, hoặc docs/specs/25.
        </p>
      </section>

      <section id="testcase" className="card">
        <h2>11. Testcase: vai trò, duyệt, trường tuỳ biến, sửa hàng loạt</h2>
        <h3>Vai trò</h3>
        <p>
          Admin gán bộ quyền ở Admin › Phân quyền. Có sẵn năm bộ quyền mẫu cho Testcase (sửa và gán như bộ thường; vai trò khác nhau giữa các dự án
          thì tạo bộ riêng):
        </p>
        <div className="table-wrap">
          <table className="guide-matrix">
            <caption className="sr-only">Quyền của từng vai trò Testcase</caption>
            <thead>
              <tr>
                <th scope="col">Vai trò</th>
                {TEST_PERMS.map(([key, label]) => (
                  <th key={key} scope="col" title={key}>
                    {label}
                  </th>
                ))}
                <th scope="col">Ghi chú</th>
              </tr>
            </thead>
            <tbody>
              {TEST_ROLES.map(([role, ...rest]) => {
                const flags = rest.slice(0, 6) as boolean[];
                const note = rest[6] as string;
                return (
                  <tr key={role}>
                    <th scope="row">{role}</th>
                    {flags.map((on, i) => (
                      <td key={TEST_PERMS[i][0]} className="guide-matrix-cell">
                        {on ? <span aria-label="Có">✓</span> : <span className="muted" aria-label="Không">—</span>}
                      </td>
                    ))}
                    <td className="small">{note}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
        <ul>
          <li>
            <strong>Sửa</strong> không còn gồm xoá: xoá testcase, bộ, thư mục cần quyền <strong>Xoá</strong>. Bộ quyền có sẵn đã được chép quyền
            nên không ai mất quyền.
          </li>
          <li>
            <strong>Quản lý</strong>: test run, plan, milestone, cấu hình, trường tuỳ biến và cài đặt dự án của Testcase.
          </li>
        </ul>
        <h3>Duyệt testcase</h3>
        <ol>
          <li>
            Bật “Bắt buộc duyệt testcase” ở Testcase › Thiết lập › Cài đặt dự án. Khi tắt, ai có quyền Sửa vẫn đặt được “Đã duyệt” như cũ.
          </li>
          <li>
            Người viết mở testcase ở trạng thái Nháp và bấm <strong>Gửi duyệt</strong>: testcase chuyển sang <strong>Chờ duyệt</strong>.
          </li>
          <li>
            Người có quyền Duyệt lọc trạng thái “Chờ duyệt” để thấy hàng đợi, mở từng testcase và bấm <strong>Duyệt</strong> hoặc{" "}
            <strong>Từ chối</strong> (bắt buộc ghi lý do; testcase quay về Nháp).
          </li>
          <li>Sửa nội dung một testcase đã duyệt thì nó quay về Chờ duyệt. Mỗi lần duyệt / từ chối hiện ở mục “Duyệt” của testcase.</li>
        </ol>
        <h3>Trường tuỳ biến</h3>
        <ul>
          <li>
            Người có quyền Quản lý thêm trường ở Thiết lập › Trường tuỳ biến, cho testcase hoặc cho kết quả test: văn bản, số, ngày, chọn một,
            chọn nhiều, có / không, người dùng, link. Giới hạn theo bộ và template; đánh dấu bắt buộc; đặt giá trị mặc định.
          </li>
          <li>
            Trường bắt buộc chặn lưu testcase (hoặc ghi kết quả có trạng thái) khi để trống. Nhập Sheet, AI, CI không bị chặn: điền giá trị mặc
            định.
          </li>
          <li>Bảng testcase: nút “Cột” chọn trường hiện thành cột (nhớ trong trình duyệt); bộ lọc “Trường” lọc theo một trường.</li>
          <li>Lưu trữ một trường thì nó ẩn khỏi form, cột, bộ lọc; giá trị cũ vẫn giữ và khôi phục được.</li>
        </ul>
        <h3>Sửa hàng loạt và hoàn tác</h3>
        <ul>
          <li>
            Chọn nhiều testcase để đổi trạng thái, ưu tiên, type, cách chạy, thêm / bỏ nhãn, đặt ước tính, thêm mã Jira, đặt trường tuỳ biến,
            chuyển hoặc sao chép sang bộ / thư mục khác (bản sao là Nháp, có mã mới).
          </li>
          <li>
            “Tìm &amp; thay” (menu ⋯ của bộ) thay chữ trong tiêu đề, bước, kết quả mong đợi…; bấm Xem trước để thấy từng chỗ đổi rồi mới Áp
            dụng.
          </li>
          <li>
            “Tìm trùng lặp bằng AI” nhóm testcase trùng, gợi ý testcase nên giữ; áp dụng thì testcase thừa chuyển sang Lỗi thời.
          </li>
          <li>
            Sau mỗi lần sửa hàng loạt có nút <strong>Hoàn tác</strong> (trong 24 giờ, xem lại ở “Lịch sử thao tác hàng loạt”). Testcase đã bị
            sửa sau đó được giữ nguyên và báo lại; xoá hàng loạt không hoàn tác được.
          </li>
        </ul>
      </section>

      <section id="testcase-more" className="card">
        <h2>12. Testcase: BDD, phiên khám phá, baseline, nhật ký thay đổi, tham số</h2>
        <h3>BDD (Gherkin) và file .feature</h3>
        <ul>
          <li>
            Chọn template <strong>BDD (Gherkin)</strong> khi soạn testcase và viết kịch bản bằng từ khoá tiếng Anh: <code>Scenario</code>,{" "}
            <code>Scenario Outline</code> + <code>Examples</code>, <code>Given / When / Then / And / But</code>, bảng <code>| … |</code>, docstring{" "}
            <code>&quot;&quot;&quot;</code>, tag <code>@…</code>. Cú pháp được kiểm tra ngay khi gõ; lỗi ghi rõ số dòng, bấm vào để nhảy tới dòng đó.
          </li>
          <li>Với Scenario Outline, bảng Examples chính là bảng tham số của testcase: mỗi dòng Examples là một test trong run.</li>
          <li>
            Menu ⋯ của bộ: <strong>Nhập .feature</strong> (chọn nhiều file, mỗi Feature thành một thư mục, mỗi Scenario một testcase; tag{" "}
            <code>@C12</code> cập nhật đúng testcase C12) và <strong>Xuất .feature</strong> (một file mỗi thư mục, nhiều thư mục thì tải file zip).
          </li>
          <li>
            Chọn nhiều testcase rồi dùng “AI chuyển dạng” để đổi sang BDD hoặc về dạng từng bước: xem trước / sau từng testcase, tick cái muốn đổi rồi
            Áp dụng; có nút Hoàn tác như sửa hàng loạt.
          </li>
        </ul>
        <h3>Phiên khám phá (Exploratory)</h3>
        <ul>
          <li>Template <strong>Khám phá</strong> gồm sứ mệnh (charter) và timebox (ô ước tính).</li>
          <li>
            Khi chạy test trong run: bấm <strong>Bắt đầu phiên</strong> để bấm giờ (tạm dừng / tiếp tục được), ghi nhanh từng phát hiện dạng Ghi chú,
            Lỗi, Câu hỏi hoặc Ý tưởng; mỗi ghi chú gắn mốc thời gian trong phiên.
          </li>
          <li>Ghi kết quả thì các ghi chú phiên được đính vào kết quả; để trống “Thời gian chạy” thì lấy thời gian của phiên.</li>
        </ul>
        <h3>Baseline</h3>
        <ul>
          <li>
            Tab <strong>Baseline</strong>: người có quyền Quản lý chụp lại nội dung một bộ (hoặc vài thư mục) cho một bản phát hành, có thể gắn
            milestone. Baseline không sửa được, chỉ tạo và xoá; testcase Lỗi thời không vào baseline.
          </li>
          <li>
            <strong>So sánh</strong> hai baseline cùng bộ, hoặc baseline với testcase hiện tại: thấy testcase thêm, bỏ, và trường nào đổi (trước / sau).
          </li>
          <li>
            Tạo test run chọn <strong>Từ baseline</strong>: test lấy nội dung đúng như lúc chụp, không đổi theo testcase hiện tại. Run hiện nhãn
            “Baseline: tên”.
          </li>
        </ul>
        <h3>Nhật ký thay đổi</h3>
        <ul>
          <li>
            Tab <strong>Nhật ký thay đổi</strong> (cần quyền Quản lý test): mọi lần tạo / sửa / xoá bộ, thư mục, testcase, run, plan, milestone,
            cấu hình, trường, trạng thái, baseline… kèm người làm và giá trị trước / sau của từng cột đổi.
          </li>
          <li>Lọc theo người, loại đối tượng, hành động, khoảng ngày hoặc tìm chữ; “Tải thêm” để xem cũ hơn; tải Excel / CSV để lưu trữ.</li>
          <li>Nhật ký giữ ít nhất 1 năm. Kết quả test không ghi vào đây (đã có lịch sử kết quả của từng test).</li>
        </ul>
        <h3>Tham số</h3>
        <ul>
          <li>
            Mục <strong>Tham số</strong> trong form testcase: bảng tối đa 20 cột, 200 dòng; tên cột gồm chữ không dấu, số, <code>_</code>. Dán một
            vùng copy từ Excel / Google Sheet vào ô bất kỳ để điền nhanh.
          </li>
          <li>
            Viết <code>{"{{tên_cột}}"}</code> trong tiêu đề, tiền điều kiện, bước, kết quả mong đợi, dữ liệu test hoặc kịch bản BDD. Biến không có cột
            được báo đỏ và chặn lưu.
          </li>
          <li>
            Đưa vào test run thì mỗi dòng là một test, tiêu đề thêm <code>[cột=giá trị]</code> và chip “dòng n”; nội dung hiện đã thay giá trị. CI:
            <code>C12[3]</code> ghi kết quả cho dòng 3, <code>C12</code> cho mọi dòng.
          </li>
        </ul>
      </section>

      <p style={{ marginTop: 16 }}>
        <Link href="/">← Về trang chính</Link>
      </p>
        </div>
      </div>
    </>
  );
}
