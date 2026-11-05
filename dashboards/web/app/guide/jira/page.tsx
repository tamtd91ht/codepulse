import type { Metadata } from "next";
import Link from "next/link";
import type { ReactNode } from "react";

import { GuideNav } from "@/components/GuideNav";

export const metadata: Metadata = { title: "Tích hợp Jira" };

const SECTIONS = [
  { id: "overview", label: "Tổng quan" },
  { id: "read", label: "CodePulse đọc gì" },
  { id: "mapping", label: "Quy đổi trường" },
  { id: "points", label: "Story Points" },
  { id: "subtask", label: "Sub-task" },
  { id: "link", label: "Nối issue với MR" },
  { id: "prod", label: "Lỗi production và blocked" },
  { id: "people", label: "Nối người" },
  { id: "sync", label: "Đồng bộ" },
  { id: "admin", label: "Dành cho admin" },
];

const MAPPING: [dpi: string, jira: ReactNode][] = [
  ["Trạng thái", <>Status category <code>Done</code> → đóng, còn lại → mở.</>],
  ["Ngày đóng", <>Resolution date; không có thì lúc đổi status category; không có nữa thì lần cập nhật cuối (chỉ khi đã Done).</>],
  [
    "Loại",
    <>
      Issue type: Story / New Feature → <code>feature</code>, Bug → <code>bug</code>, Improvement → <code>refactor</code>, Task → <code>chore</code>,
      Documentation → <code>docs</code>. Loại khác → <code>chore</code>. Sub-task theo loại của issue cha.
    </>,
  ],
  ["Độ phức tạp", <>Story Points, xem mục Story Points.</>],
  ["Lỗi production", <>Bug có label <code>production</code> hoặc <code>prod-bug</code>, hoặc issue type <code>Incident</code>. Mức theo Priority.</>],
  ["Hạn", <>Due date; không có thì ngày kết thúc sprint gần nhất; không có nữa thì release date sớm nhất của Fix Version.</>],
  ["Milestone", <>Tên sprint gần nhất, không có thì Fix Version đầu tiên.</>],
  ["Blocked", <>Đang Flagged (Impediment), hoặc status có tên chứa “Blocked”.</>],
  ["Label", <>Label của Jira, cộng label <code>blocked::…</code> như trên.</>],
  ["Thời gian", <>Original estimate và time spent.</>],
  ["Người làm", <>Assignee (Jira Data Center chỉ có một người được giao).</>],
  ["Link", <><code>&lt;địa chỉ Jira&gt;/browse/ABC-123</code></>],
];

const POINTS: [range: string, level: string, score: string][] = [
  ["trên 0 đến 1", "XS", "1"],
  ["trên 1 đến 2", "S", "2"],
  ["trên 2 đến 3", "M", "3"],
  ["trên 3 đến 5", "L", "5"],
  ["trên 5", "XL", "8 (trần)"],
];

/** The Jira Data Center integration document (docs/specs/27); readable without signing in. */
export default function JiraGuidePage() {
  return (
    <>
      <div className="page-head">
        <div className="titles">
          <div>
            <div className="eyebrow">Guide</div>
            <h1>Tích hợp Jira</h1>
            <div className="sub">Dùng Jira Data Center làm nguồn task cho DPI: Story Points, loại issue, lỗi production và blocked.</div>
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
          <section id="overview" className="card">
            <h2>1. Tổng quan</h2>
            <p>
              Công ty dùng một Jira Data Center chung, mỗi đội là một dự án Jira. Khi Admin chọn nguồn task là <strong>Jira</strong>, các chỉ số
              DPI về giao việc (A1, A2, C2, C3), lỗi production (B2) và báo cáo blocker (R4) đọc issue từ Jira thay cho GitLab Issues. Code, MR,
              pipeline và review vẫn lấy từ GitLab.
            </p>
            <p>
              CodePulse <strong>chỉ đọc</strong> Jira: không tạo ticket, không đổi trạng thái, không comment. DPI chỉ đọc một nguồn nên không tính
              trùng; dữ liệu GitLab Issues cũ vẫn giữ.
            </p>
          </section>

          <section id="read" className="card">
            <h2>2. CodePulse đọc gì</h2>
            <ul>
              <li>Issue của các dự án Jira Admin đã bật, trừ Epic.</li>
              <li>Tiêu đề, loại, trạng thái, Story Points, sprint, Fix Version, hạn, label, Priority, Flagged, assignee, thời gian ước lượng và đã làm.</li>
              <li>Không đọc mô tả, comment hay file đính kèm.</li>
              <li>
                Trên GitLab: key Jira (dạng <code>ABC-123</code>) trong tiêu đề, mô tả và tên nhánh của MR. Chỉ lưu key, không lưu mô tả.
              </li>
            </ul>
          </section>

          <section id="mapping" className="card">
            <h2>3. Quy đổi trường</h2>
            <p>Mặc định như bảng dưới. Admin sửa được ở DPI › Jira › Quy đổi trường.</p>
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>DPI</th>
                    <th>Lấy từ Jira</th>
                  </tr>
                </thead>
                <tbody>
                  {MAPPING.map(([dpi, jira]) => (
                    <tr key={dpi}>
                      <td>{dpi}</td>
                      <td>{jira}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>

          <section id="points" className="card">
            <h2>4. Story Points → độ phức tạp</h2>
            <p>
              Trường <strong>Story Points</strong> (Admin đổi được tên trường, ví dụ “Story point estimate”) quy về mức độ phức tạp, làm tròn lên mức
              gần nhất:
            </p>
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Story Points</th>
                    <th>Mức</th>
                    <th className="num">Điểm A1</th>
                  </tr>
                </thead>
                <tbody>
                  {POINTS.map(([range, level, score]) => (
                    <tr key={level}>
                      <td>{range}</td>
                      <td>{level}</td>
                      <td className="num">{score}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <ul>
              <li>Để trống hoặc 0: issue không có độ phức tạp và độ tin cậy A1 giảm. Hãy ước lượng mọi issue trước khi làm.</li>
              <li>
                Trên 8 điểm vẫn chỉ tính 8. Trang DPI › Sẵn sàng dữ liệu đếm các issue này với lời nhắc <strong>nên tách issue</strong>.
              </li>
              <li>Mục tiêu toàn công ty: ít nhất 70% issue đóng có Story Points.</li>
            </ul>
          </section>

          <section id="subtask" className="card">
            <h2>5. Sub-task</h2>
            <ul>
              <li>Sub-task có Story Points được tính như issue thường.</li>
              <li>
                Sub-task <strong>không có</strong> Story Points thì không tính trong DPI (A1, A2, C2), vì điểm nằm ở story cha. Muốn chia điểm cho
                nhiều người, đặt Story Points trên từng sub-task.
              </li>
              <li>Loại của sub-task theo loại của issue cha.</li>
            </ul>
          </section>

          <section id="link" className="card">
            <h2>6. Nối issue với MR</h2>
            <p>CodePulse tìm key Jira trong MR để biết MR làm issue nào (C3, truy vết lỗi production):</p>
            <ul>
              <li>
                <strong>Tên nhánh</strong> có key: <code>feature/ABC-123-mo-ta</code>, <code>fix/ABC-456-loi-dang-nhap</code>.
              </li>
              <li>
                <strong>Tiêu đề MR</strong> có key: <code>ABC-123 Thêm bộ lọc theo ngày</code>.
              </li>
              <li>Key trong tiêu đề hoặc tên nhánh nghĩa là MR <strong>làm</strong> issue đó; key chỉ có trong mô tả chỉ là nhắc tới.</li>
              <li>
                Commit nên bắt đầu bằng key, ví dụ <code>ABC-123: thêm validate ngày</code> (quy ước để đọc lịch sử; CodePulse nối theo MR).
              </li>
              <li>
                Chỉ key của dự án Jira đã đăng ký mới được nối, nên chuỗi như <code>UTF-8</code> hay <code>SHA-256</code> không bị nhầm.
              </li>
            </ul>
          </section>

          <section id="prod" className="card">
            <h2>7. Lỗi production và blocked</h2>
            <ul>
              <li>
                Lỗi production: tạo <strong>Bug</strong> và gắn label <code>production</code> (hoặc <code>prod-bug</code>), hoặc dùng issue type{" "}
                <code>Incident</code>.
              </li>
              <li>
                Mức lỗi theo Priority: Highest / Blocker → P1, High / Critical → P2, còn lại → P3. Lỗi vào hàng chờ RCA ở DPI › Lỗi production; chỉ
                lỗi đã xác nhận RCA mới trừ điểm B2.
              </li>
              <li>
                Blocked: bật <strong>Flag</strong> (Impediment) trên issue, hoặc chuyển sang status có tên chứa “Blocked”. Báo cáo blocker (R4) hiện
                lý do là <code>impediment</code> hoặc tên status.
              </li>
            </ul>
          </section>

          <section id="people" className="card">
            <h2>8. Nối người</h2>
            <ul>
              <li>Mỗi người dùng Jira là một tài khoản trong CodePulse; issue tính cho người được giao (assignee).</li>
              <li>
                Tự nối khi email Jira trùng email đăng nhập CodePulse, hoặc tên đăng nhập Jira cộng miền email công ty trùng. Liên kết có sẵn không bị
                ghi đè.
              </li>
              <li>
                Tài khoản chưa nối hiện ở danh sách tài khoản chưa gán của Admin và ở DPI › Sẵn sàng dữ liệu (% assignee đã nối). Issue của tài khoản
                chưa nối không tính cho ai.
              </li>
            </ul>
          </section>

          <section id="sync" className="card">
            <h2>9. Đồng bộ</h2>
            <ul>
              <li>Mặc định 15 phút một lần, lấy issue cập nhật kể từ lần trước. Lần đầu lấy lùi 180 ngày.</li>
              <li>Đồng bộ chạy khi đã cấu hình và có dự án bật, kể cả khi nguồn task vẫn là GitLab, để dữ liệu sẵn sàng trước khi chuyển.</li>
              <li>Issue chuyển sang dự án khác vẫn là một issue (key mới). Issue bị xoá trên Jira vẫn giữ trong CodePulse.</li>
              <li>Lỗi đồng bộ hiện ở DPI › Jira và DPI › Sẵn sàng dữ liệu; lần sau tự làm lại.</li>
            </ul>
          </section>

          <section id="admin" className="card">
            <h2>10. Dành cho admin</h2>
            <ol>
              <li>
                Tạo tài khoản dịch vụ <strong>chỉ đọc</strong> trên Jira với quyền Browse projects ở các dự án cần lấy, rồi tạo Personal Access Token.
              </li>
              <li>
                Mở <Link href="/admin#jira">Admin › Jira</Link> (hoặc <Link href="/dpi">DPI</Link> › <strong>Jira</strong>), thẻ <strong>Kết nối</strong>:
                nhập URL Jira và Personal Access Token; tuỳ chọn CA nội bộ (PEM), chu kỳ đồng bộ (mặc định 15 phút), số ngày lấy lùi lần đầu (180),
                timeout (20 giây). Bấm <strong>Kiểm tra kết nối</strong> để thử ngay giá trị đang nhập, rồi <strong>Lưu</strong>. Không cần khởi động
                lại: lịch đồng bộ đọc cấu hình mới trong vòng một phút.
              </li>
              <li>
                Kết quả kiểm tra cho biết phiên bản Jira và các trường Story Points, Sprint, Flagged tìm được. Trường báo “không tìm thấy” thì sửa tên ở
                Quy đổi trường.
              </li>
              <li>
                Biến môi trường <code>JIRA_BASE_URL</code>, <code>JIRA_TOKEN</code>, <code>JIRA_CA_BUNDLE</code>, <code>JIRA_SYNC_INTERVAL_MIN</code>,{" "}
                <code>JIRA_BACKFILL_DAYS</code>, <code>JIRA_TIMEOUT_S</code> là tuỳ chọn: chỉ dùng cho giá trị chưa lưu trên web; giá trị lưu trên web luôn
                được ưu tiên. <strong>Dùng lại biến môi trường</strong> xoá các giá trị đã lưu. Đổi sang máy chủ Jira khác thì phải nhập lại token.
              </li>
              <li>
                <strong>Thêm dự án</strong>, bật và gán mỗi dự án Jira cho một dự án CodePulse (để lọc báo cáo và phân quyền), rồi Lưu. Dự án chưa
                gán chỉ admin thấy trong báo cáo cấp team.
              </li>
              <li>
                Bấm <strong>Đồng bộ ngay</strong>, đợi xong, xem DPI › Sẵn sàng dữ liệu (% Story Points, % assignee đã nối).
              </li>
              <li>
                Chuyển <strong>Nguồn task</strong> sang Jira, rồi chạy lại dữ liệu DPI (tab Tổng quan) cho các tháng cần tính lại.
              </li>
            </ol>
            <p className="muted">
              Token lưu trên web được mã hoá (APP_ENCRYPTION_KEY), không bao giờ hiện lại trên giao diện hay trong log. Mọi thay đổi cấu hình Jira đều ghi audit. Chi tiết: docs/specs/27.
            </p>
          </section>

          <p style={{ marginTop: 16 }}>
            <Link href="/guide">← Về Hướng dẫn sử dụng</Link>
          </p>
        </div>
      </div>
    </>
  );
}
