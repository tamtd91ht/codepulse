import type { Metadata } from "next";
import Link from "next/link";

export const metadata: Metadata = { title: "Hướng dẫn" };

const SECTIONS = [
  { id: "intro", label: "CodePulse đo gì" },
  { id: "access", label: "Ai xem được gì" },
  { id: "developer", label: "Developer" },
  { id: "lead", label: "Lead" },
  { id: "admin", label: "Admin" },
  { id: "telegram", label: "Bot Telegram" },
  { id: "metrics", label: "Đọc các chỉ số" },
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
            sync gần nhất); bấm vào ô để mở ngăn chi tiết: đổi tên, repo của dự án với đầy đủ nút Sync / Webhook /
            Review / Bỏ theo dõi, thêm repo có sẵn vào dự án, bot Telegram và webhook thông báo của dự án. Nút{" "}
            <strong>Danh sách</strong> chuyển sang bảng toàn bộ repo để lọc, chọn nhiều và thao tác hàng loạt.
          </li>
          <li>Xoá dự án chỉ bỏ cách nhóm; repo và lịch sử review vẫn giữ.</li>
        </ul>
        <h3>Quy tắc review theo dự án</h3>
        <p>
          Trong ngăn chi tiết của một dự án (Admin → Dự án &amp; Repo → bấm ô dự án) có mục <strong>Quy tắc review</strong>:
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
          <strong>Lịch sử review</strong>. Trên Telegram, lead/admin bấm <strong>↻ Cho chọn mục đã review</strong> trong danh sách chọn. Lần cũ chỉ bị
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
                  của bạn; lead mới review được người khác)
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
                <td>Các MR đang mở chưa được review (lead)</td>
              </tr>
              <tr>
                <td>
                  <code>/status</code> · <code>/critical</code> · <code>/score</code>
                </td>
                <td>Review gần đây, lỗi nghiêm trọng đang mở, điểm kỳ gần nhất</td>
              </tr>
            </tbody>
          </table>
        </div>
        <p className="muted">
          Quyền giống dashboard: thành viên review việc của mình, lead review người trong team mình, admin mọi người. Lệnh
          review tốn ngân sách LLM và được ghi audit log.
        </p>
      </section>

      <section id="metrics" className="card">
        <h2>7. Đọc các chỉ số</h2>
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

      <p style={{ marginTop: 16 }}>
        <Link href="/">← Về trang chính</Link>
      </p>
        </div>
      </div>
    </>
  );
}
