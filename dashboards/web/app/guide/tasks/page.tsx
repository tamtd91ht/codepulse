import type { Metadata } from "next";
import Link from "next/link";
import type { ReactNode } from "react";

import { GuideNav } from "@/components/GuideNav";
import { CopyBlock } from "@/components/McpConnect";

export const metadata: Metadata = { title: "Quản lý Task" };

const SECTIONS = [
  { id: "overview", label: "Tổng quan" },
  { id: "enable", label: "Bật Task cho dự án" },
  { id: "frame", label: "Khung trang và điều hướng" },
  { id: "create", label: "Tạo task" },
  { id: "model", label: "Loại, trạng thái, ưu tiên, liên kết" },
  { id: "board", label: "Board" },
  { id: "filter", label: "Thanh lọc và bộ lọc đã lưu" },
  { id: "list", label: "Danh sách" },
  { id: "backlog", label: "Backlog và sprint" },
  { id: "timeline", label: "Timeline và nhiều dự án" },
  { id: "detail", label: "Chi tiết task" },
  { id: "git", label: "Gắn code với task" },
  { id: "quality", label: "Chất lượng và phát hành" },
  { id: "mywork", label: "Việc của tôi" },
  { id: "notify", label: "Thông báo" },
  { id: "automation", label: "Quy tắc tự động" },
  { id: "reports", label: "Báo cáo và Sức khoẻ sprint" },
  { id: "keys", label: "Ctrl+K và phím tắt" },
  { id: "permission", label: "Phân quyền" },
  { id: "faq", label: "Câu hỏi thường gặp" },
];

/** One key cap, as in the app's "?" shortcut list. */
function K({ children }: { children: ReactNode }) {
  return <kbd className="nl-kbd">{children}</kbd>;
}

function Table({ head, rows }: { head: string[]; rows: ReactNode[][] }) {
  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr>
            {head.map((h) => (
              <th key={h}>{h}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={i}>
              {r.map((c, j) => (
                <td key={j}>{c}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

const TEMPLATES: ReactNode[][] = [
  [<strong key="n">Scrum</strong>, "Cần làm → Đang làm → Đang review → Xong", "Đội làm theo sprint; mọi loại task; có tab Backlog."],
  [<strong key="n">Kanban</strong>, "Cần làm → Đang làm → Xong", "Việc chảy liên tục, không sprint; Board hiện mọi task chưa xong và task xong 14 ngày qua; không có tab Backlog."],
  [
    <strong key="n">QA / xử lý lỗi</strong>,
    "Mới → Đang xác minh → Đã sửa → Đã đóng",
    "Đội QA nhận bug: MR sửa đưa bug sang Đã sửa, tester đóng sau khi kiểm lại (merge không tự đóng).",
  ],
  [<strong key="n">Mobile</strong>, "Như Scrum", "Như Scrum, thêm trường “Nền tảng” (iOS / Android / Cả hai)."],
];

const QUICK: ReactNode[][] = [
  [<code key="c">@an</code>, "Người làm: phần trước @ của email, hoặc một chữ trong tên"],
  [<code key="c">#bug</code>, "Loại: bug, story, task, epic, subtask"],
  [<code key="c">!cao</code>, "Mức ưu tiên: tên hoặc mã của mức, gõ không dấu cũng được"],
  [<code key="c">3sp</code>, "Story point"],
  [<code key="c">^OCX-12</code>, <>Task cha (hoặc <code>^12</code> trong cùng dự án): task cha là Story / Task / Bug thì task mới thành Sub-task</>],
];

const GIT: ReactNode[][] = [
  [<>Tạo nhánh <code>feature/OCX-12-dang-nhap</code> (hoặc bấm <strong>Tạo nhánh</strong> trên task)</>, "Nối nhánh; task sang trạng thái “bắt đầu” (Đang làm)"],
  [<>Commit <code>feat(auth): gửi OTP [OCX-12]</code></>, "Nối commit (liên quan), kèm điểm review"],
  [<>Mở MR có <code>OCX-12</code> ở tiêu đề hoặc tên nhánh</>, "Nối MR; task sang trạng thái “review” (Đang review)"],
  [<>MR đó merge vào nhánh mặc định, hoặc commit có dòng <code>Fixes: OCX-12</code> vào nhánh mặc định</>, "Task sang Xong"],
  ["MR đóng mà không merge (task đang review)", "Task về lại Đang làm"],
];

const BRANCH = "git checkout -b feature/OCX-12-dang-nhap-otp";
const COMMIT = `fix(auth): chặn gửi OTP lặp [OCX-12]

Fixes: OCX-12`;
const SMART = `feat(auth): giới hạn số lần gửi OTP [OCX-12]

#time 1h30m #comment Đã thêm rate limit 3 lần/phút #in-review`;

const SHORTCUTS: [keys: ReactNode, what: string, where: string][] = [
  [<><K>Ctrl</K> + <K>K</K></>, "Mở bảng lệnh (task, trang, lệnh)", "Mọi trang"],
  [<K key="k">/</K>, "Tìm task (trên màn có thanh lọc: vào ô tìm)", "Mọi trang"],
  [<K key="k">N</K>, "Tạo task", "Mọi trang"],
  [<><K>G</K> rồi <K>B</K></>, "Mở Board", "Mọi trang"],
  [<><K>G</K> rồi <K>M</K></>, "Mở Việc của tôi", "Mọi trang"],
  [<K key="k">?</K>, "Hiện / ẩn danh sách phím tắt", "Mọi trang"],
  [<><K>1</K> … <K>7</K></>, "Board, Backlog, Danh sách, Báo cáo, Sức khoẻ, Phiên bản, Timeline", "Trang Task"],
  [<K key="k">C</K>, "Tạo task (khi không mở task nào)", "Trang Task"],
  [<><K>/</K> hoặc <K>F</K></>, "Vào ô tìm của thanh lọc", "Board, Danh sách, Backlog"],
  [<><K>Shift</K> + <K>F</K></>, "Mở “+ Lọc”", "Board, Danh sách, Backlog"],
  [<><K>Shift</K> + <K>X</K></>, "Xoá lọc", "Board, Danh sách, Backlog"],
  [<K key="k">V</K>, "Mở Hiển thị", "Board, Danh sách, Backlog"],
  [<><K>J</K> / <K>K</K></>, "Task kế tiếp / trước trong danh sách đang hiện", "Ngăn chi tiết task"],
  [<K key="k">Esc</K>, "Đóng ngăn chi tiết; trên Board: bỏ chọn card", "Ngăn chi tiết, Board"],
  [<K key="k">E</K>, "Sửa mô tả", "Khi đang mở một task"],
  [<K key="k">A</K>, "Chọn người làm", "Khi đang mở một task"],
  [<K key="k">S</K>, "Chọn trạng thái", "Khi đang mở một task"],
  [<K key="k">C</K>, "Viết bình luận", "Khi đang mở một task"],
  [<K key="k">.</K>, "Mở bảng lệnh với các lệnh trên task", "Khi đang mở một task"],
  [<><K>Ctrl</K> + <K>Enter</K></>, "Gửi bình luận", "Ô bình luận"],
  [<><K>Ctrl</K> + <K>Z</K></>, "Hoàn tác thay đổi vừa làm (trong 10 giây, ngoài ô nhập)", "Khi đang có toast Hoàn tác"],
];

const PERMS: [perm: string, what: string, member: string, manager: string][] = [
  ["Xem task", "Thấy menu Task, Board, Backlog, báo cáo; theo dõi task", "✓", "✓"],
  ["Tạo task", "Tạo task, sub-task, dùng AI tách việc", "✓", "✓"],
  ["Sửa task", "Tiêu đề, mô tả, trường, sprint, phiên bản, AI soạn", "của mình", "dự án"],
  ["Giao task", "Đổi người làm; tạo task giao cho người khác", "của mình", "dự án"],
  ["Chuyển trạng thái", "Đổi trạng thái, kéo thả trên Board", "của mình", "dự án"],
  ["Xoá task", "Xoá task (mức “của mình” = task mình tạo)", "—", "dự án"],
  ["Bình luận", "Bình luận; sửa, xoá bình luận của mình", "✓", "✓"],
  ["Đính kèm", "Tải file, dán ảnh; xoá file mình tải", "✓", "✓"],
  ["Ghi giờ", "Ghi giờ làm; xoá giờ mình ghi", "của mình", "dự án"],
  ["Liên kết", "Liên kết task, nối / gỡ commit, MR, tạo nhánh", "của mình", "dự án"],
  ["Kiểm duyệt", "Sửa, xoá bình luận, file, giờ làm của người khác", "—", "dự án"],
  ["Sprint", "Lập, sửa, bắt đầu, hoàn tất, xoá sprint; đặt sức chứa", "—", "dự án"],
  ["Quản trị Task", "Bật Task, mã, loại, trạng thái, luật git, DoD, quy tắc, phiên bản, trường tuỳ biến", "—", "dự án"],
];

const NOTIFY: [event: string, telegram: string][] = [
  ["Được giao task", "có"],
  ["Được nhắc tên (@)", "có"],
  ["Task mình theo dõi đổi trạng thái", "—"],
  ["Bình luận trên task của mình", "—"],
  ["Hết bị chặn", "có"],
  ["Nhắc quá hạn / sắp tới hạn", "có"],
  ["MR chờ mình review", "có"],
  ["Testcase Failed trên task của mình", "có"],
  ["Cảnh báo khẩn (Bug Cao nhất, code critical)", "có"],
];

const RULES: [name: string, when: ReactNode][] = [
  ["Đóng task cha thì hỏi đóng sub-task (bật sẵn)", "Chuyển task cha sang Xong khi còn sub-task mở: hỏi có đóng luôn không."],
  ["Sub-task xong hết → task cha sang review", "Sub-task cuối cùng xong thì task cha sang trạng thái review."],
  ["Bắt đầu task chưa có người làm → giao cho người bắt đầu", "Người chuyển task chưa giao sang Đang làm thành người làm."],
  ["Bug ưu tiên cao nhất → báo quản lý", "Báo quản lý dự án trong hộp thư và nhóm Telegram của dự án."],
  ["Code còn critical → báo người làm", "Khi task vào review / Xong và mỗi ngày, nếu code của task còn finding critical."],
  ["Hết bị chặn → báo người làm", "Task chặn vừa xong và không còn gì chặn task kia."],
  ["Nằm ở review quá lâu → nhắc", "Task ở review quá N giờ (mặc định 48): nhắc người làm và người tạo."],
  ["Quá hạn → nhắc người làm", "Mỗi ngày một lần."],
  ["Sắp tới hạn → nhắc người làm", "Còn N ngày tới hạn (mặc định 1)."],
  ["MR merge vào nhánh release → gắn phiên bản", <>MR merge vào <code>release/v2.1</code> hoặc <code>release-v2.1</code> trùng tên phiên bản: gắn phiên bản đó cho task.</>],
  ["Task quá tuổi cột → báo quản lý", "Task nằm ở một cột lâu hơn mức “Cảnh báo sau (giờ)” của cột."],
  ["Testcase Failed khi đang review → về Đang làm", "Testcase phủ task Failed trong lúc task đang review."],
  ["Sprint sắp hết mà còn nhiều điểm → báo quản lý", "Còn ≤ 2 ngày mà hơn 40% điểm chưa xong (chỉnh được)."],
  ["Task không người làm trong sprint → báo quản lý", "Task trong sprint đang chạy chưa ai nhận quá N ngày (mặc định 3)."],
];

/** The Task module user guide (docs/specs/31); readable without signing in. */
export default function TasksGuidePage() {
  return (
    <>
      <div className="page-head">
        <div className="titles">
          <div>
            <div className="eyebrow">Guide</div>
            <h1>Quản lý Task</h1>
            <div className="sub">
              Quản lý việc của dự án ngay trong CodePulse: Board, Backlog, sprint, và trạng thái chạy theo nhánh, commit, MR.
            </div>
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
              Menu <Link href="/tasks">Task</Link> là công cụ quản lý việc giống Jira (Board, Backlog, sprint, phiên bản, báo cáo), nhưng mỗi task{" "}
              <strong>biết code của nó</strong>: nhánh, commit, MR, điểm review, finding critical còn mở, pipeline, đã lên production chưa. Bạn ghi mã
              task (ví dụ <code>OCX-12</code>) vào tên nhánh, commit, MR là đủ; trạng thái task tự chạy theo git, không ai phải kéo thẻ bằng tay.
            </p>
            <ul>
              <li>
                <strong>Độc lập với Jira</strong>: Task là dữ liệu riêng của CodePulse, không ghi gì sang Jira. Dự án đang dùng Jira có thể nhập issue
                sang rồi ngừng dùng Jira (mục 2).
              </li>
              <li>
                <strong>DPI đọc task</strong>: khi admin chọn <em>DPI hàng tháng › Cấu hình › Nguồn task</em> là <strong>Task CodePulse</strong>, task
                xong (lý do “Đã xong”) là đầu ra của người được giao, story point quyết định độ phức tạp, Bug có nhãn <code>production</code> là lỗi
                production. Epic và task đóng với lý do khác (Không làm, Trùng, Lỗi thời…) không tính.
              </li>
              <li>
                <strong>Claude Code</strong> (MCP) đọc được task của bạn (<code>list_my_tasks</code>, <code>get_task</code>); token có ô{" "}
                <em>Cập nhật task</em> còn chuyển trạng thái, bình luận, ghi giờ. Xem <Link href="/guide/mcp">Tích hợp MCP</Link>.
              </li>
            </ul>
          </section>

          <section id="enable" className="card">
            <h2>2. Bật Task cho dự án</h2>
            <p>
              Người quản lý Task của dự án (Lead / Manager dự án, hoặc người có quyền <em>Quản trị Task</em>) mở <Link href="/tasks">Task</Link>, chọn dự
              án chưa bật (ghi “chưa bật”) và đi theo hướng dẫn 5 bước:
            </p>
            <ol>
              <li>
                <strong>Mã và loại</strong>: mã dự án 2–10 ký tự in hoa, bắt đầu bằng chữ (ví dụ <code>OCX</code>), duy nhất trong hệ thống; task đánh
                số <code>OCX-1</code>, <code>OCX-2</code>… Mã chỉ đổi được khi dự án chưa có task.
              </li>
              <li>
                <strong>Mẫu</strong>: chọn một mẫu có sẵn hoặc một mẫu đã lưu từ dự án khác.
              </li>
              <li>
                <strong>Thành viên</strong>: người đã có quyền vào dự án, và gợi ý những người commit vào repo của dự án 90 ngày qua mà chưa có quyền.
                Manager dự án / admin bấm cấp quyền xem; người khác chỉ xem và nhờ họ.
              </li>
              <li>
                <strong>Xác nhận</strong> rồi bấm <strong>Bật Task</strong>.
              </li>
              <li>
                <strong>Jira (tuỳ chọn)</strong>: chỉ hiện khi dự án có issue Jira đã đồng bộ, để nhập sang.
              </li>
            </ol>
            <Table head={["Mẫu", "Trạng thái", "Hợp với"]} rows={TEMPLATES} />
            <p className="muted small">
              Mọi thứ sửa được sau ở ⚙ › <em>Cài đặt Task của dự án</em>: Mã và loại (cả chế độ bảng Scrum / Kanban, nút <strong>Áp mẫu</strong> và{" "}
              <strong>Lưu cấu hình thành mẫu</strong>), Trạng thái, Ưu tiên, Luật git, Trường tuỳ biến, DoD, Nhập từ Jira, Quy tắc tự động, Chuyển
              khỏi Jira. Thay đổi gom vào thanh ghim ở đáy “Có thay đổi chưa lưu” với <strong>Lưu</strong> / <strong>Hoàn tác</strong>; riêng Luật
              git tự lưu ngay.
            </p>

            <h3>Chuyển từ Jira</h3>
            <ul>
              <li>
                <strong>Nhập từ Jira</strong> (Cài đặt): xem trước rồi nhập issue Jira đã đồng bộ của dự án. Bug / incident thành Bug, feature thành
                Story, còn lại Task; issue đã đóng vào Xong, còn mở vào Cần làm. Giữ tiêu đề, nhãn, story point, hạn, người làm; MR / commit đã nối đi
                theo. Mỗi issue chỉ nhập một lần.
              </li>
              <li>
                <strong>Mã Jira cũ vẫn dùng được</strong>: commit / MR ghi <code>OMX-5</code> của issue đã nhập vẫn nối vào task mới. Thẻ{" "}
                <em>Chuyển đổi khỏi Jira</em> liệt kê ai còn dùng mã cũ trong 7 ngày qua; người viết được nhắc mỗi ngày một lần “Dùng mã mới”.
              </li>
              <li>
                <strong>Ngừng dùng Jira</strong>: khi đã nhập hết và 14 ngày không ai dùng mã cũ (chưa đạt thì phải ghi lý do). Sau đó “Tạo bug” của
                testcase luôn thành task CodePulse. CodePulse không khoá được Jira: nhờ admin Jira đặt project ở chế độ chỉ đọc. Có nút{" "}
                <strong>Dùng lại Jira</strong>.
              </li>
              <li>
                <strong>Checklist bật Task cho Lead</strong>: WIP và cảnh báo tuổi cột, DoD, quy tắc tự động (nút <strong>Bật 5 quy tắc gợi ý</strong>),
                sprint đầu tiên, nhập Jira, đủ thành viên. Mỗi mục tự tích từ dữ liệu thật. Trên Board, người quản lý thấy chip{" "}
                <strong>Checklist x/y</strong> (ẩn được) cho tới khi xong.
              </li>
              <li>
                <strong>Dữ liệu demo</strong>: <em>Tạo dữ liệu demo</em> sinh bộ task “[Mẫu] …” cho buổi hướng dẫn; <em>Xoá dữ liệu demo</em> chỉ xoá
                đúng các task đó.
              </li>
            </ul>
            <p>
              Admin và người quản lý Task xem <Link href="/tasks/adoption">Tình trạng bật Task</Link> (menu ⚙): dự án nào đã bật, mẫu nào, bao nhiêu
              task, hoạt động gần nhất, bao nhiêu người xem được.
            </p>
          </section>

          <section id="frame" className="card">
            <h2>3. Khung trang và điều hướng</h2>
            <p>Mọi màn Task có chung một hàng trên cùng:</p>
            <ul>
              <li>
                <strong>Task / &lt;Dự án&gt; ▾</strong>: bấm tên dự án để đổi dự án (gõ tên hoặc mã, không cần dấu; ↑ ↓ Enter). Dự án được nhớ cho lần
                sau.
              </li>
              <li>
                <strong>Tab</strong>: Board, Backlog, Danh sách, Báo cáo, Sức khoẻ, Phiên bản, Timeline (phím <K>1</K>…<K>7</K>). Dự án Kanban không có
                Backlog. Màn hẹp thì các tab sau vào menu <strong>Thêm ▾</strong>.
              </li>
              <li>
                <strong>⚙</strong>: <em>Cài đặt Task của dự án</em> (người quản lý) và <em>Tình trạng bật Task</em>.
              </li>
              <li>
                <strong>Của tôi</strong> mở <Link href="/tasks/me">Việc của tôi</Link>; nút xanh <strong>+ Tạo</strong> (phím <K>C</K>) mở form tạo task.
              </li>
            </ul>
            <p>
              Địa chỉ trang giữ dự án, tab, bộ lọc và task đang mở, nên gửi link cho đồng nghiệp là họ thấy đúng màn bạn đang xem. Thanh trên cùng của
              ứng dụng có nút <strong>Tìm / lệnh</strong> (<K>Ctrl</K> <K>K</K>) và nút <strong>?</strong> (danh sách phím tắt).
            </p>
          </section>

          <section id="create" className="card">
            <h2>4. Tạo task</h2>
            <h3>Form tạo task</h3>
            <p>
              Bấm <strong>+ Tạo</strong>, phím <K>C</K> (trang Task) hoặc <K>N</K> (mọi trang). Form có Loại, Ưu tiên, <strong>Tiêu đề</strong> (bắt buộc),
              Mô tả (markdown), Đính kèm, Người làm, Task cha, Sprint, Hạn, Story point, Nhãn. Enter để tạo.
            </p>
            <ul>
              <li>
                Mô tả có <strong>mẫu theo loại</strong>: Story “Là … tôi muốn … để …” và tiêu chí chấp nhận; Bug các bước tái hiện, mong đợi / thực tế,
                môi trường. Đổi loại thì mẫu đổi theo khi bạn chưa sửa mô tả.
              </li>
              <li>
                <strong>Đính kèm</strong>: <em>Chọn file, ảnh</em>, kéo thả vào form, hoặc chụp màn hình rồi <K>Ctrl</K> <K>V</K> vào ô mô tả: ảnh hiện
                ngay trong mô tả. Mỗi file tối đa 20 MB.
              </li>
              <li>Không có quyền giao việc thì chỉ giao được cho chính mình.</li>
            </ul>

            <h3>Tạo nhanh trên Board</h3>
            <p>
              Cuối mỗi cột (trừ cột Xong, và khi Board không chia swimlane) có <strong>+ Tạo nhanh</strong>. Gõ một dòng rồi Enter: task vào đúng cột đó
              và sprint đang chạy.
            </p>
            <CopyBlock text="Sửa lỗi OTP @an #bug !cao 3sp ^OCX-12" label="Ví dụ một dòng tạo nhanh" />
            <Table head={["Ký hiệu", "Ý nghĩa"]} rows={QUICK} />
            <ul>
              <li>Khi gõ, giá trị nhận ra hiện thành chip xem trước dưới ô; ký hiệu không khớp bị gạch chân đỏ và nằm lại trong tiêu đề.</li>
              <li>
                <code>@an</code> khớp nhiều người (hoặc <code>!cao</code> khớp nhiều mức): chọn một giá trị hoặc <strong>Bỏ</strong> ở chip, rồi{" "}
                <strong>Tạo task</strong> hoặc Enter.
              </li>
            </ul>

            <h3>Sau khi tạo: Mở · Hoàn tác</h3>
            <p>
              Góc dưới hiện toast <strong>Đã tạo OCX-12 · Mở · Hoàn tác</strong> trong 10 giây. <strong>Mở</strong> mở task;{" "}
              <strong>Hoàn tác</strong> gỡ task vừa tạo nhầm, khi: bạn là người tạo, trong 2 phút, task chưa có bình luận, giờ làm, liên kết, commit /
              MR, file đính kèm, sub-task, và chưa ai khác sửa. Không đạt thì toast ghi lý do.
            </p>
          </section>

          <section id="model" className="card">
            <h2>5. Loại, trạng thái, ưu tiên, liên kết</h2>
            <ul>
              <li>
                <strong>Loại</strong>: Epic (nhóm lớn, không lên Board), Story, Task, Bug, Sub-task. Sub-task đi theo sprint và phiên bản của task cha.
                Story point của sprint chỉ tính Story / Task / Bug.
              </li>
              <li>
                <strong>Trạng thái</strong> thuộc một trong ba nhóm Cần làm / Đang làm / Xong. Người quản lý đặt tên, thứ tự cột, luồng chuyển (“Được
                chuyển tới”), WIP và “Cảnh báo sau (giờ)” ở Cài đặt › Trạng thái; dự án cũ có nút <strong>Đổi tên sang tiếng Việt</strong>.
              </li>
              <li>
                <strong>Lý do đóng</strong>: vào một trạng thái nhóm Xong cần một lý do: <strong>Đã xong</strong>, Không làm, Trùng, Không tái hiện được,
                Lỗi thời. Chỉ “Đã xong” tính là việc hoàn thành (cycle time, velocity, DPI). Ở chi tiết task, hộp <em>Lý do đóng</em> hiện khi bạn chọn
                trạng thái Xong; trên Board kéo vào Xong là “Đã xong”, kèm dòng “Lý do khác: …” để đổi bằng một bấm.
              </li>
              <li>
                <strong>Mức ưu tiên</strong> riêng của dự án (Cài đặt › Ưu tiên): tên, màu, mũi tên, thứ tự, mức mặc định. Mặc định có Cao nhất, Cao,
                Trung bình, Thấp, Thấp nhất. Bug production mức Cao nhất / Cao là P1 / P2 trong DPI.
              </li>
              <li>
                <strong>Liên kết task</strong>: Chặn / Bị chặn bởi, Liên quan, Trùng với, Nhân bản từ. Task bị một task chưa xong chặn hiện ⛔ trên card
                và trong khối Sức khoẻ.
              </li>
              <li>
                <strong>Epic và sub-task</strong>: đặt Task cha khi tạo, thêm sub-task (với Epic là “Việc con”) ngay trong chi tiết task, hoặc{" "}
                <strong>AI tách việc</strong> để AI đề xuất 3–12 việc con, bạn chọn việc nào tạo.
              </li>
            </ul>
          </section>

          <section id="board" className="card">
            <h2>6. Board</h2>
            <ul>
              <li>
                Cột theo trạng thái. Dự án Scrum: Board là <strong>sprint đang chạy</strong>; nhãn bên phải thanh lọc ghi “tên sprint · còn N ngày” (bấm
                để mở Sức khoẻ). Không có sprint đang chạy, hoặc dự án Kanban: Board hiện mọi task chưa xong và task xong 14 ngày qua.
              </li>
              <li>
                Đầu cột ghi số task, tổng SP; cột có <strong>WIP</strong> ghi <code>n/WIP</code>, đỏ khi vượt (chỉ cảnh báo, không chặn). Bấm nút thu gọn
                để cột thành một dải hẹp; vẫn thả card vào được.
              </li>
              <li>
                Mỗi card hiện <strong>một</strong> tín hiệu quan trọng nhất: ⛔ bị chặn › critical › quá hạn › nằm cột quá lâu › 🔁 MR bị đóng ›
                DoD thiếu (khi dự án bắt buộc DoD). Task đã Xong không hiện tín hiệu.
              </li>
              <li>
                <strong>⧉ Hiển thị</strong> (phím <K>V</K>): Swimlane (Không chia, Theo epic, Theo task cha, Theo người làm, Theo ưu tiên), Gom sub-task
                vào card cha, Mật độ <strong>Gọn</strong> / <strong>Thoáng</strong>, Trường trên card, Ẩn cột rỗng, Thu gọn cột Done. Lựa chọn nhớ theo
                bạn.
              </li>
              <li>
                <strong>Kéo thả</strong> card giữa các cột và trong cột. Cột không nằm trong luồng chuyển của task mờ đi. Sau mỗi lần chuyển có toast{" "}
                <strong>Hoàn tác</strong> 10 giây (hoặc <K>Ctrl</K> <K>Z</K>).
              </li>
              <li>
                <strong>Bàn phím</strong>: Tab tới card, mũi tên đi giữa các card; <K>Space</K> nhấc card, ← → sang cột kế tiếp được phép, ↑ ↓ đổi vị
                trí, <K>Space</K> thả, <K>Esc</K> huỷ.
              </li>
              <li>
                Bấm một card để chọn nó (viền màu), <K>Ctrl</K> <K>K</K> lúc đó hiện lệnh trên task này. Bấm tiêu đề để mở ngăn chi tiết.
              </li>
              <li>
                <strong>Thời gian thực</strong>: thay đổi của người khác, của git và của quy tắc tự động hiện ngay trên Board, Danh sách và chi tiết task,
                không cần tải lại trang.
              </li>
            </ul>
          </section>

          <section id="filter" className="card">
            <h2>7. Thanh lọc và bộ lọc đã lưu</h2>
            <p>Board, Danh sách và Backlog dùng chung một thanh lọc một hàng:</p>
            <ul>
              <li>
                <strong>Ô tìm</strong>: gõ chữ để tìm theo mã, tiêu đề, cả mô tả và bình luận (không cần dấu). Gõ một câu lọc như{" "}
                <em>bug cao chưa giao</em>, <em>quá hạn của tôi</em>, <em>sprint này</em> thì ô hiện gợi ý “Enter để lọc: Loại Bug · Ưu tiên Cao…”; Enter
                hoặc bấm gợi ý để biến câu thành chip.
              </li>
              <li>
                <strong>Của tôi</strong>: chỉ task giao cho bạn. Chồng avatar <strong>Người làm</strong>: bấm để chọn nhiều người.
              </li>
              <li>
                <strong>Chip ghim</strong> (mặc định Loại, Ưu tiên): chọn nhanh giá trị. <strong>+ Lọc</strong> mở mọi trường (trạng thái, sprint, phiên
                bản, nhãn, hạn, lý do đóng, người tạo, ngày tạo / cập nhật / đóng, SP, giờ trong cột, có MR, testcase failed, trường tuỳ biến…), kèm số
                task theo từng giá trị (“Bug · 3”), nút ghim để đưa trường lên thanh (tối đa 2) và ba nút nhanh <em>Bị chặn</em>,{" "}
                <em>Có critical</em>, <em>DoD chưa đạt</em>.
              </li>
              <li>
                Chip đọc “Trường: giá trị ✕”; nhiều giá trị cùng trường là “hoặc”, khác trường là “và”. Thanh chật thì chip dư gom vào{" "}
                <strong>+N bộ lọc ▾</strong>; <strong>Xoá lọc</strong> bỏ hết. Số kết quả bên phải ghi “· đã lọc”.
              </li>
              <li>
                <strong>★ Bộ lọc</strong>: <em>Lưu bộ lọc hiện tại</em> với tên, <em>Ai thấy</em> (Chỉ mình tôi / Team của tôi / Mọi người xem dự án),{" "}
                <em>Ghim lên Board làm lọc nhanh</em>, <em>Nhận tóm tắt</em> (hằng ngày 8:00 hoặc thứ Hai 8:00, qua Telegram nếu đã liên kết, không thì
                hộp thông báo). <em>Chia sẻ, ghim, nhận tóm tắt</em> sửa các cài đặt đó sau; <em>Khôi phục mặc định</em> về bộ lọc ban đầu.
              </li>
              <li>Bộ lọc đã ghim hiện thành nút 📌 trên Board; bấm để lọc, bấm lần nữa để bỏ.</li>
              <li>
                Bộ lọc nhớ theo bạn, dự án và màn, và nằm trên địa chỉ trang: <strong>gửi link</strong> là người nhận thấy đúng bộ lọc đó.
              </li>
            </ul>
          </section>

          <section id="list" className="card">
            <h2>8. Danh sách</h2>
            <ul>
              <li>Bảng mọi task theo bộ lọc, tải thêm khi cuộn. Bấm một dòng để mở ngăn chi tiết.</li>
              <li>
                <strong>Sửa ngay tại ô</strong>: Trạng thái, Ưu tiên, Người làm, SP, Hạn. Mỗi lần sửa có Hoàn tác 10 giây.
              </li>
              <li>
                <strong>Chọn nhiều</strong> (ô chọn đầu dòng) thì thanh lọc thành thanh thao tác hàng loạt: Trạng thái, Giao (hoặc Bỏ giao), Sprint (hoặc
                Backlog), Ưu tiên, Nhãn (Thêm nhãn / Bỏ nhãn), ⋯ Phiên bản và <strong>Xoá N task</strong> (có hộp xác nhận, không hoàn tác được). Task
                không đổi được (luồng chuyển, critical, DoD) bị bỏ qua và được báo lại; phần còn lại hoàn tác được trong 10 giây.
              </li>
              <li>
                <strong>Hiển thị</strong>: Mật độ Gọn / Thoáng, Nhóm theo (trạng thái, người làm, sprint, ưu tiên, loại), Cột hiển thị.
              </li>
              <li>
                Nút <strong>Xuất CSV theo bộ lọc hiện tại</strong> tải tối đa 5.000 dòng (mã, tiêu đề, loại, trạng thái, ưu tiên, người làm, sprint, hạn,
                SP, nhãn, lý do đóng), mở thẳng bằng Excel.
              </li>
            </ul>
          </section>

          <section id="backlog" className="card">
            <h2>9. Backlog và sprint</h2>
            <p>Chỉ dự án Scrum. Màn chia cột Epic bên trái và danh sách sprint + Backlog bên phải.</p>
            <ul>
              <li>
                <strong>Cột Epic</strong>: mỗi epic có thanh tiến độ, % xong và SP còn lại; bấm để lọc theo epic (chip “Epic: … ✕”), <em>Mọi task</em> để
                bỏ. Thu gọn được bằng ⟨⟨. Tạo epic bằng form Tạo task, loại Epic.
              </li>
              <li>
                <strong>+ Tạo sprint</strong>: tên, mục tiêu, ngày bắt đầu / kết thúc. Kéo task từ Backlog vào sprint (hoặc giữa các sprint, đổi thứ tự).
              </li>
              <li>
                <strong>Sức chứa</strong>: đầu sprint ghi “10/18 SP”, đỏ ⚠ khi kéo vào nhiều hơn sức chứa. Bấm chip SP để thấy mục tiêu, sức chứa (tự
                tính = trung bình 3 sprint gần nhất, hoặc <em>Đặt sức chứa tay</em> / <em>Dùng TB 3 sprint</em>) và <strong>Tải theo người</strong> (đỏ khi
                một người ôm hơn 40% SP).
              </li>
              <li>
                <strong>Bắt đầu</strong>: hộp xác nhận chạy <em>Kiểm tra trước khi bắt đầu</em>: task chưa ước lượng, chưa có người làm, bị chặn bởi task
                ngoài sprint, vượt sức chứa, một người quá tải. Có cảnh báo thì nút thành <strong>Vẫn bắt đầu</strong> (không chặn). Mỗi dự án chỉ một
                sprint đang chạy; SP lúc bắt đầu là cam kết.
              </li>
              <li>Task thêm / bỏ ra sau khi bắt đầu được ghi lại (“+3 task sau khi bắt đầu”).</li>
              <li>
                <strong>Hoàn tất</strong>: hộp hiện <strong>Đánh giá sprint</strong> (đã xong, chuyển sang sau, thêm sau khi bắt đầu, bug, finding,
                cycle time) và chọn <em>Chuyển task chưa xong sang</em> sprint khác hoặc Backlog. Sau khi hoàn tất, <strong>Tạo trang retro</strong> tạo
                một trang ở Tài liệu dự án với ba mục Điều tốt / Cần cải thiện / Hành động.
              </li>
              <li>
                <strong>Hiển thị</strong>: Hiện sprint rỗng, Hiện cột Epic, Mật độ. Thanh lọc ở Backlog lọc ngay trên các dòng đã tải.
              </li>
            </ul>
          </section>

          <section id="timeline" className="card">
            <h2>10. Timeline và nhiều dự án</h2>
            <ul>
              <li>
                Tab <strong>Timeline</strong>: các epic và việc con theo ngày, hàng tuần, hàng phiên bản (◆), hàng sprint và vạch hôm nay. Phần đậm của
                thanh là đã xong, đoạn đỏ là quá hạn; mũi tên là quan hệ “chặn”, đỏ khi task chặn xong sau hạn của task bị chặn (kèm dòng “Bị chặn quá
                hạn”). Lùi / tới 4 tuần, <strong>Hôm nay</strong>.
              </li>
              <li>
                <strong>Kéo mép phải</strong> thanh để đổi hạn; <strong>kéo thanh</strong> lên một dải sprint để chuyển sprint. Không dùng chuột: nút{" "}
                <strong>Đổi lịch</strong> ở mỗi dòng. Cột tên kéo rộng được.
              </li>
              <li>
                Trang <Link href="/tasks/overview">Tổng quan Task</Link> (nút <em>Tổng quan mọi dự án</em> ở tab Sức khoẻ): mỗi dự án một dòng: màu sức khoẻ
                (Ổn / Cần chú ý / Rủi ro), sprint đang chạy, dự phóng, bug cao nhất, lỗi critical, lead time, phiên bản kế tiếp, xu hướng 4 sprint. Chế
                độ <strong>Timeline</strong> xem epic của nhiều dự án cùng lúc (chỉ đọc).
              </li>
            </ul>
          </section>

          <section id="detail" className="card">
            <h2>11. Chi tiết task</h2>
            <p>
              Bấm task ở Board, Danh sách, Backlog hay Việc của tôi mở <strong>ngăn bên phải</strong>, view phía sau vẫn dùng được. Đầu ngăn có ▲ ▼ (hoặc{" "}
              <K>J</K> / <K>K</K>) sang task trước / sau, ⤢ mở toàn trang (<code>/tasks/OCX-12</code>), ↗ sao chép link, ✕ đóng (<K>Esc</K>). Kéo mép trái
              để đổi độ rộng.
            </p>
            <ul>
              <li>
                <strong>Dải thuộc tính</strong>: Trạng thái, Người làm, Ưu tiên, Sprint, SP, Hạn. Các trường khác (Loại, Nhãn, Thành phần, Phiên bản,
                Ước lượng, Đã làm, trường tuỳ biến, Theo dõi, AI tách việc / AI tóm tắt, Xoá task) nằm trong <strong>+ Thêm trường</strong>. Đổi trường có
                Hoàn tác 10 giây.
              </li>
              <li>
                <strong>Khối Sức khoẻ</strong> ngay dưới, một màu tổng <em>Ổn / Cần chú ý / Có vấn đề</em>: ⛔ bị chặn bởi task nào, MR (đã merge / đang
                mở), điểm review, critical / high, pipeline, đã deploy chưa, quá hạn, tiêu chí có testcase, Testcase Passed, DoD x/y ·{" "}
                <em>Xem thiếu gì</em>. Bấm một mục để mở đúng chỗ (MR trên GitLab, review, test run…).
              </li>
              <li>
                Mô tả (phím <K>E</K>; Viết / Xem trước), Sub-task, Liên kết, <strong>Code</strong> (nhánh, MR, commit; <em>Liên kết commit</em>,{" "}
                <em>Tạo nhánh</em>), Tiêu chí chấp nhận, Testcase, Definition of Done, Đính kèm.
              </li>
              <li>
                <strong>Dòng thời gian</strong> gộp mọi thứ, lọc bằng chip Tất cả / Bình luận / Code / Test / Thay đổi / Giờ làm. Mỗi thay đổi là một câu
                tiếng Việt; thay đổi do git hay quy tắc ghi “Tự động”. Chip <strong>Giờ làm</strong> có bảng giờ và form <em>Ghi giờ</em> (ví dụ{" "}
                <code>1h30m</code>).
              </li>
              <li>
                <strong>Ô bình luận</strong> (phím <K>C</K>): <code>@</code> gợi ý người của dự án để nhắc tên, <code>#</code> gợi ý task để chèn mã,{" "}
                <K>Ctrl</K> <K>Enter</K> gửi. Dán ảnh hoặc kéo thả file vào ô thì ảnh hiện ngay trong bình luận.
              </li>
              <li>
                <strong>Dán ảnh chụp màn hình</strong> (<K>Ctrl</K> <K>V</K>) khi đang xem task (con trỏ không ở ô nhập) thì ảnh thành file đính kèm.
              </li>
              <li>
                <strong>Theo dõi</strong> để nhận thông báo khi task đổi trạng thái hoặc có bình luận. <strong>AI tóm tắt</strong> tóm tắt tiến độ từ mô
                tả, bình luận, commit, MR, review.
              </li>
            </ul>
          </section>

          <section id="git" className="card">
            <h2>12. Gắn code với task</h2>
            <p>
              Chỉ cần ghi mã task viết hoa (<code>OCX-12</code>) ở tên nhánh, commit hoặc MR, trong repo thuộc đúng dự án. CodePulse tự nối và tự chuyển
              trạng thái theo <strong>Luật git</strong> của dự án:
            </p>
            <Table head={["Bạn làm", "Task"]} rows={GIT} />
            <h3>Đặt tên nhánh</h3>
            <p>
              <code>&lt;loại&gt;/&lt;MÃ&gt;-&lt;mô-tả&gt;</code>. Nút <strong>Tạo nhánh</strong> trên task tạo sẵn nhánh <code>feature/OCX-12-…</code>{" "}
              (Bug: <code>bugfix/…</code>) trên GitLab từ nhánh mặc định; lệnh Ctrl+K <em>Sao chép tên nhánh</em> cho bạn tên đó để tạo ở máy.
            </p>
            <CopyBlock text={BRANCH} label="Ví dụ tạo nhánh" />
            <h3>Commit “làm xong” và commit “liên quan”</h3>
            <ul>
              <li>
                <strong>Làm xong (fixes)</strong>: dòng <code>Fixes:</code> / <code>Closes:</code> / <code>Resolves:</code> ghi mã, câu “fixes OCX-12”,
                hoặc tiêu đề <code>fix(...)</code> / <code>hotfix(...)</code> / <code>bugfix(...)</code> có mã. MR có mã ở tiêu đề hoặc tên nhánh cũng là
                “làm xong”.
              </li>
              <li>
                <strong>Liên quan (refs)</strong>: mã ở chỗ khác, ví dụ <code>feat(x): … [OCX-12]</code>, hoặc chỉ nhắc trong mô tả MR.
              </li>
              <li>
                Task chỉ sang Xong khi code “làm xong” <strong>vào nhánh mặc định</strong> (MR merge, hoặc push commit <code>Fixes:</code>). Commit ở
                nhánh phụ chỉ được nối.
              </li>
            </ul>
            <CopyBlock text={COMMIT} label="Ví dụ commit sửa bug" />
            <h3>Smart commit</h3>
            <p>
              Thêm lệnh vào message khi chính bạn push commit của mình: <code>#time 1h30m</code> ghi giờ, <code>#comment …</code> thêm bình luận,{" "}
              <code>#&lt;trạng thái&gt;</code> chuyển trạng thái (<code>#in-progress</code>, <code>#in-review</code>, <code>#done</code>,{" "}
              <code>#xong</code>, <code>#todo</code>, hoặc tên trạng thái viết thường không dấu như <code>#dang-lam</code>). Lệnh bạn không có quyền
              làm bị bỏ qua, commit vẫn được nối.
            </p>
            <CopyBlock text={SMART} label="Ví dụ smart commit" />
            <h3>Luật git của dự án</h3>
            <p>
              Cài đặt › Luật git (tự lưu): <em>Bắt đầu khi có commit</em>, <em>Review khi mở MR</em>, <em>Xong khi merge</em>, <em>Mở lại khi đóng MR</em>,{" "}
              <em>Chặn Done khi còn critical</em>, <em>Bình luận vào MR</em> (ghi link task vào MR), và trạng thái đích cho “bắt đầu”, “review”, “xong”.
              Luật không bao giờ kéo task đã Xong lùi lại. Commit / MR tạo trước ngày bật Task hơn 1 ngày không được nối.
            </p>
            <p className="muted small">
              Nối tay khi quên ghi mã: phần Code › <em>Liên kết commit</em> (tìm theo SHA ≥ 4 ký tự hoặc chữ trong message, hoặc chọn repo + số MR,
              chọn “fixes: làm xong task” hay “refs: có liên quan”). Trang review của commit / MR hiện các task nối với nó.
            </p>
          </section>

          <section id="quality" className="card">
            <h2>13. Chất lượng và phát hành</h2>
            <ul>
              <li>
                <strong>Tiêu chí chấp nhận</strong>: AC1, AC2… trong chi tiết task (tích đạt, sửa, đổi thứ tự). Mỗi tiêu chí gắn được testcase và hiện
                trạng thái theo testcase đó, không có thì “chưa có testcase”.
              </li>
              <li>
                <strong>Testcase</strong> phủ task: gắn bằng mã <code>C123</code>, chọn “Cả task” hoặc một tiêu chí. Hiện kết quả lần chạy gần nhất và tổng
                Passed / Failed. Testcase Failed vào <em>Việc của tôi</em> của người làm.
              </li>
              <li>
                <strong>Bug từ testcase</strong>: trong test run, <strong>Tạo bug</strong> (cả kết quả, hoặc nút cạnh từng bước Failed) tạo Bug trong
                Task với mô tả đủ bước, môi trường, log, đính kèm; người xử lý mặc định là người làm task mà testcase phủ. Khi mọi bug của test đã xong,
                ô Bug hiện “Cần retest” và người test được nhắc. Nơi nhận bug chọn ở Testcase › Cài đặt dự án.
              </li>
              <li>
                <strong>Definition of Done</strong> (Cài đặt › DoD): chọn từ Có MR, MR đã merge, Điểm review đạt (ngưỡng tuỳ chỉnh), Không còn critical,
                Pipeline không lỗi, Testcase đều Passed. Mỗi task tự tích danh sách này. Bật <em>bắt buộc</em> thì không chuyển được sang Xong (lý do
                “Đã xong”) khi còn thiếu, kể cả git tự chuyển.
              </li>
              <li>
                <strong>Phiên bản</strong> (tab Phiên bản): <strong>+ Phiên bản</strong> với tên, mô tả, ngày phát hành dự kiến; gắn task vào phiên bản.
                Cột Sẵn sàng kiểm: mọi task đã xong, không còn bug Cao / Cao nhất mở, testcase Passed, MR đã merge; chip “Chưa sẵn sàng (n) ▸” liệt kê
                thứ đang chặn. <strong>Phát hành</strong> khi sẵn sàng; menu ⋯ có <em>Release notes</em> (markdown từ task đã xong), Sửa, Xoá và{" "}
                <em>Vẫn phát hành…</em> (phải ghi lý do).
              </li>
            </ul>
          </section>

          <section id="mywork" className="card">
            <h2>14. Việc của tôi</h2>
            <p>
              Nút <strong>Của tôi</strong> hoặc <K>G</K> rồi <K>M</K>. Gom việc của bạn ở mọi dự án, tự làm mới mỗi phút:
            </p>
            <ul>
              <li>
                <strong>Cần xử lý ngay</strong>: task quá hạn, code còn finding critical, MR chờ bạn review, @nhắc chưa đọc, testcase Failed, test cần
                chạy lại vì bug đã xong.
              </li>
              <li>
                <strong>Đang làm</strong>, <strong>Chờ người khác</strong> (bị chặn), <strong>Cần làm</strong> (sprint đang chạy trước),{" "}
                <strong>Xong tuần này</strong>, <strong>Sprint của tôi</strong> (còn mấy ngày, SP / task bạn đã xong).
              </li>
              <li>Ô tìm theo mã / tiêu đề, chip Dự án khi việc thuộc nhiều dự án, Hiển thị (mật độ, ẩn / hiện hai nhóm cuối).</li>
            </ul>
          </section>

          <section id="notify" className="card">
            <h2>15. Thông báo</h2>
            <p>
              Avatar góc phải › <Link href="/settings/notifications">Cài đặt thông báo</Link>, thẻ <strong>Thông báo Task</strong>. Mỗi sự kiện chọn{" "}
              <strong>Hộp thư</strong>, <strong>Telegram</strong> hoặc <strong>Tắt</strong>:
            </p>
            <Table head={["Sự kiện", "Chọn được Telegram"]} rows={NOTIFY.map(([e, t]) => [e, t])} />
            <ul>
              <li>
                <strong>Cách nhận</strong>: Ngay khi có, hoặc Gộp thành một bản tóm tắt mỗi ngày. Nhiều cập nhật trên cùng task trong 10 phút gộp thành
                một thông báo.
              </li>
              <li>
                <strong>Giờ yên lặng</strong> và <strong>Yên lặng cả ngày</strong> thứ Bảy / Chủ nhật: thông báo được giữ lại và gửi sau. Bug ưu tiên Cao
                nhất, code còn critical và sự cố production vẫn báo ngay.
              </li>
              <li>
                <strong>Tóm tắt cuối ngày</strong> (mặc định tắt, 17:30): việc xong hôm nay, việc quá hạn, việc ngày mai, MR chờ bạn review.
              </li>
              <li>
                Telegram cần liên kết tài khoản trước (gửi <code>/link MÃ</code> cho bot, xem mục Bot Telegram ở{" "}
                <Link href="/guide#telegram">Hướng dẫn sử dụng</Link>). Tin có nút Mở, Đã xem, Ẩn 1 ngày; task đang Cần làm có thêm nút{" "}
                <strong>Bắt đầu</strong>.
              </li>
              <li>Trên chuông, “Tạm ẩn 1 ngày” cất một thông báo chưa đọc tới mai. Admin đặt <em>Mặc định của tổ chức</em> cho người chưa tự chỉnh.</li>
            </ul>
          </section>

          <section id="automation" className="card">
            <h2>16. Quy tắc tự động</h2>
            <p>
              Cài đặt › <strong>Quy tắc tự động</strong>: thư viện quy tắc có sẵn, bật / tắt theo dự án, vài quy tắc có tham số. Quy tắc chạy dưới tên
              hệ thống, theo đúng luồng trạng thái; nhắc mỗi task tối đa một lần mỗi ngày. Quy tắc theo lịch có nút <strong>Chạy thử</strong> (xem sẽ tác
              động tới task nào, không đổi gì); chế độ <strong>Nhật ký</strong> ghi mọi việc quy tắc đã làm.
            </p>
            <Table head={["Quy tắc", "Khi nào"]} rows={RULES.map(([n, w]) => [n, w])} />
          </section>

          <section id="reports" className="card">
            <h2>17. Báo cáo và Sức khoẻ sprint</h2>
            <ul>
              <li>
                Tab <strong>Báo cáo</strong>: chọn khoảng ngày (7 / 30 / 90 ngày, Sprint này, Sprint trước). Dải chỉ số (cycle time trung vị và p85, task
                xong, task tạo, giờ làm; bấm một ô để mở Danh sách đã lọc) và các biểu đồ Burndown, Velocity, Tạo và hoàn thành, Theo trạng thái, Giờ làm
                theo người, Đóng theo lý do, Theo ưu tiên, Theo người làm. <strong>Hiển thị</strong> ẩn / hiện từng biểu đồ.
              </li>
              <li>
                Tab <strong>Sức khoẻ</strong> (sprint đang chạy, hoặc chọn một sprint đã đóng): <strong>Dự báo</strong> (burndown, dự phóng, “Dự kiến xong
                85–95% SP”), <strong>Kẹt</strong> (task nằm một trạng thái quá lâu, MR chờ review quá 24 giờ, task bị chặn), <strong>Tải</strong> (SP và
                task mở của từng người, xếp theo tên), <strong>Chất lượng</strong> (critical đang mở, bug cao nhất, bug tạo, task mở lại, thay đổi phạm
                vi). Màu tổng luôn kèm chữ Ổn / Cần chú ý / Rủi ro.
              </li>
              <li>Sprint đã đóng hiện Đánh giá sprint và nút Tạo trang retro.</li>
            </ul>
          </section>

          <section id="keys" className="card">
            <h2>18. Ctrl+K và phím tắt</h2>
            <h3>Bảng lệnh Ctrl+K</h3>
            <ul>
              <li>
                Tìm task theo mã, tiêu đề hoặc nội dung, gõ không dấu và viết tắt cũng được (“gct” → “Giao cho tôi”). Ô trống hiện task và trang vừa mở.
              </li>
              <li>
                Tiền tố: <code>&gt;</code> chỉ lệnh, <code>#</code> chỉ task, <code>@</code> người của dự án (chọn một người để xem việc đang mở của họ).
              </li>
              <li>
                Khi đang mở một task hoặc đã chọn một card, nhóm <strong>Lệnh trên OCX-12</strong> lên đầu: Giao cho tôi, Giao cho…, Chuyển sang…, Thêm vào
                sprint…, Gắn phiên bản…, Đổi mức ưu tiên…, Thêm nhãn…, Sao chép link, Sao chép tên nhánh, Tạo sub-task.
              </li>
              <li>Mọi trang: Chuyển dự án…, Bộ lọc đã lưu…, Tìm người…, và mở nhanh Task, Tạo task, Việc của tôi, Review, Testcase.</li>
            </ul>
            <h3>Phím tắt</h3>
            <Table head={["Phím", "Tác dụng", "Ở đâu"]} rows={SHORTCUTS.map(([k, w, p]) => [k, w, p])} />
            <p className="muted small">
              Phím một chữ không chạy khi bạn đang gõ trong ô nhập, giữ Ctrl / Alt, hoặc đang mở hộp thoại, menu, danh sách chọn hay popover (gõ “c” khi
              một menu đang mở không tạo task). Trên Mac dùng ⌘ thay Ctrl.
            </p>
          </section>

          <section id="permission" className="card">
            <h2>19. Phân quyền</h2>
            <p>
              Quyền Task chia như permission scheme của Jira, nằm trong nhóm <strong>Task</strong> ở Admin › Phân quyền. Mặc định:
            </p>
            <Table head={["Quyền", "Cho phép", "Thành viên", "Quản lý dự án"]} rows={PERMS.map(([a, b, c, d]) => [a, b, c, d])} />
            <ul>
              <li>
                <strong>Phạm vi</strong>: <em>Của mình</em> = task mình tạo hoặc được giao; <em>Team</em> = task của người trong team mình lead / manage;{" "}
                <em>Dự án</em> = mọi task của dự án mình quản lý; <em>Tất cả</em> = mọi dự án mình xem được. Không quyền nào mở task của dự án mình không
                xem được.
              </li>
              <li>
                Bộ <strong>Lead</strong> có sẵn được sửa, giao, chuyển, ghi giờ, liên kết, kiểm duyệt task ở mức Team. Lead / Manager dự án có đủ quyền Task
                ở dự án của mình.
              </li>
              <li>
                <strong>Tester / Team QA</strong>: admin tích “Tester” ở hồ sơ một người, hoặc đánh dấu một team là team QA (team có thể tự đồng bộ thành
                viên từ một nhóm GitLab). Họ tự có bộ <strong>Tester</strong>: viết và chạy testcase mọi dự án xem được, tạo task và xử lý task của mình.
              </li>
              <li>
                Không chắc mình làm được gì? Avatar › <Link href="/me/permissions">Quyền của tôi</Link>: vai trò, bộ quyền và nguồn của từng quyền; ô{" "}
                <strong>Tôi có làm được không?</strong> chọn một quyền và một task (hoặc dự án) để biết được hay không và vì sao.
              </li>
            </ul>
          </section>

          <section id="faq" className="card">
            <h2>20. Câu hỏi thường gặp</h2>
            <h3>Tôi commit rồi mà task không đổi trạng thái?</h3>
            <p>
              Kiểm tra: mã viết hoa và đúng mã của dự án chứa repo; repo được CodePulse theo dõi; luật <em>Bắt đầu khi có commit</em> đang bật; commit
              mới hơn ngày bật Task. Task chỉ sang Xong khi code “làm xong” vào nhánh mặc định, và không bị chặn bởi DoD bắt buộc hay critical (lịch sử
              task ghi lý do “chưa chuyển Done”).
            </p>
            <h3>Kéo card sang cột khác mà không được?</h3>
            <p>
              Cột đó không nằm trong luồng chuyển của trạng thái hiện tại (cột mờ khi kéo), hoặc bạn không có quyền Chuyển trạng thái cho task này (mức
              “của mình” chỉ gồm task bạn tạo hoặc được giao). Xem lý do ở <Link href="/me/permissions">Quyền của tôi</Link>.
            </p>
            <h3>Đóng task “Không làm” có bị trừ điểm không?</h3>
            <p>Không. Task đóng với lý do khác “Đã xong” không tính là đầu ra, không vào velocity, cycle time hay DPI.</p>
            <h3>Tạo nhầm task thì xoá thế nào?</h3>
            <p>
              Trong 2 phút và task chưa có gì thêm: bấm <strong>Hoàn tác</strong> trên toast “Đã tạo”. Sau đó: <em>+ Thêm trường › Xoá task</em> (cần quyền
              Xoá task; xoá không hoàn tác được, sub-task bị xoá theo), hoặc đóng với lý do Trùng / Không làm.
            </p>
            <h3>Sao Board không thấy task của tôi?</h3>
            <p>
              Board Scrum chỉ hiện sprint đang chạy: task ở Backlog nằm ở tab Backlog. Epic không lên Board. Kiểm tra cả thanh lọc (Xoá lọc) và Hiển thị ›
              Ẩn cột rỗng / Gom sub-task vào card cha.
            </p>
            <h3>Task CodePulse và issue Jira cũ trùng mã thì sao?</h3>
            <p>
              Không trùng được: CodePulse không cho đặt mã Task trùng tiền tố issue Jira đã nhập. Mã Jira cũ của issue đã nhập vẫn trỏ về task mới.
            </p>
          </section>
        </div>
      </div>
    </>
  );
}
