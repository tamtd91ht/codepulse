import type { Metadata } from "next";
import Link from "next/link";

export const metadata: Metadata = { title: "Hướng dẫn – CodePulse" };

const SECTIONS = [
  { id: "intro", label: "CodePulse đo gì" },
  { id: "access", label: "Ai xem được gì" },
  { id: "developer", label: "Developer" },
  { id: "lead", label: "Lead" },
  { id: "admin", label: "Admin" },
  { id: "metrics", label: "Đọc các chỉ số" },
];

/** The dashboard copy of docs/USER_GUIDE.md, plus the Admin screen; readable without signing in. */
export default function GuidePage() {
  return (
    <div className="guide">
      <h1>Hướng dẫn sử dụng</h1>
      <p className="muted">CodePulse review code bằng AI và tổng hợp chất lượng code theo team và từng thành viên.</p>

      <nav className="card toc" aria-label="Mục lục">
        {SECTIONS.map((s, i) => (
          <a key={s.id} href={`#${s.id}`}>
            {i + 1}. {s.label}
          </a>
        ))}
      </nav>

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
            không phải điểm do AI tự cho. Có ít nhất một lỗi critical thì điểm tối đa là 50.
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
            gửi. Project không cho phép gửi code ra ngoài sẽ không được review.
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
          Nếu project bật tính năng này, CodePulse đăng một comment tổng hợp lên MR và cập nhật mỗi lần bạn push. Mỗi
          thứ Hai bạn nhận email báo cáo cá nhân, chỉ có dữ liệu của bạn.
        </p>
      </section>

      <section id="lead" className="card">
        <h2>4. Lead</h2>
        <ul>
          <li>
            <strong>Team</strong>: chỉ số tuần/tháng so với kỳ trước, finding theo mức độ, loại lỗi và file
            &ldquo;nóng&rdquo;, bảng thành viên với xu hướng 8 tuần. Quality Index chỉ hiện khi một người có từ 3
            review trong kỳ.
          </li>
          <li>
            <strong>Critical Board</strong>: mọi finding critical/high còn mở, lọc theo project hoặc người, cũ nhất
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
            Trên GitLab, tạo một tài khoản bot (ví dụ <code>codepulse-bot</code>) và thêm vào các project cần đo với
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
            GitLab trước khi lưu; sau đó hãy thu hồi token cũ trên GitLab.
          </li>
        </ol>
        <h3>Thêm project</h3>
        <ol>
          <li>Lấy Project ID trên GitLab: trang project, ngay dưới tên project, hoặc Settings → General.</li>
          <li>
            Ở <strong>Admin → Projects</strong>, chọn kết nối, nhập Project ID, giữ tick{" "}
            <strong>Đăng ký webhook</strong> rồi bấm <strong>Thêm project</strong>.
          </li>
          <li>
            CodePulse tự lấy dữ liệu 90 ngày gần nhất (<code>BACKFILL_DAYS</code>). Nếu bước webhook lỗi, project vẫn được thêm; bấm{" "}
            <strong>Webhook</strong> ở dòng của project để thử lại.
          </li>
          <li>
            Thêm nhiều project một lần: nhập Group ID hoặc đường dẫn group (ví dụ <code>vihat/backend</code>) rồi bấm{" "}
            <strong>Thêm cả group</strong>. Mọi project đang hoạt động trong group và subgroup được thêm; project tạo
            sau này cần thêm lại. Bot phải có quyền Reporter trên group.
          </li>
          <li>
            Bật các tuỳ chọn cho từng project: <strong>AI review</strong>, <strong>Comment lên MR</strong>,{" "}
            <strong>Cho gửi code ra LLM</strong>. Thiếu tuỳ chọn cuối thì project không được review.
          </li>
        </ol>
        <h3>Gắn tài khoản với người</h3>
        <p>
          Commit và MR được gắn với người theo email. Tài khoản chưa nhận ra được hiện ở{" "}
          <strong>Tài khoản chưa gắn người</strong>; chọn người tương ứng để gắn. Danh sách team và thành viên được
          nhập bằng CSV qua lệnh <code>codepulse identity import</code> (xem docs/RUNBOOK.md).
        </p>
        <h3>Chi phí LLM</h3>
        <p>
          Bảng <strong>Chi phí LLM theo tháng</strong> cho thấy số review, chi phí ước tính và phần trăm ngân sách
          tháng.
        </p>
      </section>

      <section id="metrics" className="card">
        <h2>6. Đọc các chỉ số</h2>
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

      <p>
        <Link href="/">← Về trang chính</Link>
      </p>
    </div>
  );
}
