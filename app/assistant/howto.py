"""Stetho's how-to knowledge (user 2026-11-09): how to use CodePulse, for "làm sao để…" questions.

Curated topics, written by the CodePulse team (not data from users, unlike the <ngu_canh>
snapshot): each has keywords and the screens it belongs to. A turn gets the topics its question
or the screen on view matches (at most MAX_TOPICS), plus the list of every topic so Stetho can
say what else it knows. The hook and CI job here are the ones on Hướng dẫn > Tích hợp Jira
(tests/assistant/test_howto.py keeps them equal).
"""

import re
import unicodedata
from dataclasses import dataclass

MAX_TOPICS = 3


@dataclass(frozen=True)
class Topic:
    id: str
    title: str
    keywords: tuple[str, ...]  # matched without accents, case-insensitively
    paths: tuple[str, ...]  # screens (path prefixes) where the topic applies
    body: str


HOOK = r"""#!/bin/sh
# CodePulse: mỗi commit ghi mã Jira (Hướng dẫn > Tích hợp Jira > Chuẩn nhánh, commit, MR).
# Thiếu mã mà tên nhánh có mã (bugfix/CRM-415-...) thì tự thêm " [CRM-415]" vào tiêu đề.
msg="$1"
subject=$(head -n1 "$msg")
case "$subject" in
  Merge*|Revert*|fixup!*|squash!*|amend!*) exit 0 ;;
esac
KEY='[A-Z][A-Z0-9_]{1,9}-[1-9][0-9]{0,8}'
if grep -v '^#' "$msg" | grep -qE "(^|[^A-Za-z0-9_-])$KEY([^A-Za-z0-9]|$)"; then
  exit 0
fi
branch=$(git symbolic-ref --short HEAD 2>/dev/null)
key=$(printf '%s' "$branch" | grep -oiE "[A-Za-z][A-Za-z0-9_]{1,9}-[1-9][0-9]{0,8}" | head -n1 | tr '[:lower:]' '[:upper:]')
if [ -n "$key" ]; then
  sed -i.bak "1s/\$/ [$key]/" "$msg" && rm -f "$msg.bak"
  exit 0
fi
echo "Commit cần mã Jira, ví dụ: fix(export): căn đúng cột [CRM-415]" >&2
echo "Commit sửa bug: thêm dòng 'Fixes: CRM-415' ở cuối message." >&2
exit 1"""

CI = r"""jira-key:
  stage: .pre
  image: alpine/git:2.45.2
  variables:
    GIT_DEPTH: 0
  rules:
    - if: $CI_PIPELINE_SOURCE == "merge_request_event"
  script:
    - git fetch origin "$CI_MERGE_REQUEST_TARGET_BRANCH_NAME"
    - |
      fail=0
      for sha in $(git rev-list --no-merges "origin/$CI_MERGE_REQUEST_TARGET_BRANCH_NAME..HEAD"); do
        if ! git log -1 --format=%B "$sha" | grep -qE '(^|[^A-Za-z0-9_-])[A-Z][A-Z0-9_]{1,9}-[1-9][0-9]{0,8}([^A-Za-z0-9]|$)'; then
          echo "Thiếu mã Jira: $(git log -1 --format='%h %s' "$sha")"
          fail=1
        fi
      done
      exit $fail"""

JIRA_STANDARD = f"""
Mục đích: mỗi thay đổi code lần ra được issue Jira của nó, mỗi bug lần ra được commit đã sửa nó.
Trang đầy đủ: Hướng dẫn › Tích hợp Jira › mục 6 (/guide/jira#link).

### Tiêu chuẩn
- **Issue**: mọi việc có một issue Jira; lỗi là issue type Bug (lỗi production thêm label `production`).
- **Nhánh**: `<loại>/<KEY>-<mô-tả>`, loại: feature, bugfix, hotfix, refactor, chore.
  Ví dụ `bugfix/CRM-415-export-lech-cot`.
- **Commit**: tiêu đề theo Conventional Commits, mã ở CUỐI trong ngoặc vuông; một commit một việc.
  Ví dụ `feat(contact): đồng bộ danh bạ Zalo [CRM-412]`.
- **Commit sửa lỗi**: loại `fix` (hoặc `hotfix`) và dòng cuối `Fixes: KEY`:
  ```
  fix(export): căn đúng cột [CRM-415]

  Fixes: CRM-415
  ```
- **Liên quan nhiều issue**: dòng `Refs: CRM-412, CRM-415` (chỉ liên quan); sửa nhiều bug: `Fixes: CRM-1, CRM-2`.
- **MR**: tiêu đề có mã (`[CRM-415] Sửa lệch cột khi export`), mô tả ghi `Closes CRM-415`.
  Squash merge: giữ tiêu đề MR làm message để mã còn trong commit trên nhánh chính.
- Dạng cũ `CRM-415: mô tả` vẫn được nhận nhưng chỉ tính là "liên quan".
- Lý do đặt mã ở cuối: giữ chuẩn Conventional Commits (commitlint, changelog) mà mã vẫn thấy
  trong `git log --oneline`; `Fixes:` là git trailer chuẩn, tách "sửa" khỏi "nhắc tới".

### CodePulse hiểu commit thế nào
- **Commit sửa**: message có dòng `Fixes:` / `Closes:` / `Resolves:` ghi mã (hoa thường đều được,
  nhiều mã cách nhau dấu phẩy), câu "Fixes CRM-415" ở đầu dòng hoặc đầu câu, hoặc tiêu đề
  `fix(...)` / `hotfix(...)` / `bugfix(...)` ghi mã.
- **Liên quan**: mã ở chỗ khác trong message (kể cả dòng `Refs:`).
- Không tính là sửa: "does not fix CRM-2", "partial fix CRM-1", "will fix … later"; commit
  `Revert "…"` chỉ là liên quan; merge commit không được nối (đã nối qua MR).
- Mã phải VIẾT HOA và thuộc dự án Jira đã đăng ký ở Admin › Jira (nên `UTF-8`, `SHA-256` không bị
  nhầm). MR cũng được đọc: tên nhánh, tiêu đề (MR "làm" issue), mô tả (chỉ nhắc tới).
- Commit mới được nối khi đồng bộ GitLab; issue mới được nối khi đồng bộ Jira (chu kỳ ở Admin ›
  Jira); commit có từ trước được đọc lại một lần sau khi nâng cấp.

### Xem kết quả ở đâu
- **Task Jira** (/jira): cột Code (MR đã merge/tổng, số commit, số commit sửa), KPI "Bug đã có bản
  sửa", lọc Code = "Bug chưa có bản sửa", bấm mã issue để xem chi tiết: MR, commit (commit sửa
  đứng trước), review và điểm của từng cái.
- Tab **Chưa gắn issue** › MR | Commit: những MR/commit trong kỳ không ghi mã, kèm tỉ lệ commit có
  mã (đo mức tuân thủ của đội).
- Trang review của một commit có dải Jira: "Commit sửa" hoặc "có nhắc tới".

### Bắt buộc theo chuẩn
Thứ tự nên làm: cài hook cho cả đội trước (tự thêm mã nên ít vướng) → theo dõi tỉ lệ ở Task Jira ›
Chưa gắn issue → bật job CI bắt buộc.

1. Hook `commit-msg` trên máy dev: chặn commit thiếu mã, tự thêm mã lấy từ tên nhánh; chạy được
   với Git Bash trên Windows. Lưu vào `.githooks/commit-msg`:
```sh
{HOOK}
```
   Cài (mỗi người một lần sau khi clone):
```sh
mkdir -p .githooks
chmod +x .githooks/commit-msg
git config core.hooksPath .githooks
```
   Hook không biết dự án Jira nào đã đăng ký, nên `SHA-256` cũng lọt qua hook; CodePulse vẫn lọc
   đúng khi nối.
2. Job GitLab CI (mọi bản GitLab), thêm vào `.gitlab-ci.yml` và bật "Pipelines must succeed":
```yaml
{CI}
```
3. GitLab Premium › Settings › Repository › Push rules:
   - commit message: `[A-Z][A-Z0-9_]{{1,9}}-[1-9][0-9]*|^Merge |^Revert `
   - tên nhánh: `^(main|master|develop|release/.+)$|^(feature|bugfix|hotfix|refactor|chore)/[A-Za-z][A-Za-z0-9_]{{1,9}}-[0-9]+(-[a-z0-9-]+)?$`

### Câu hỏi thường gặp
- *Quên ghi mã, chưa push*: `git commit --amend` sửa message (hoặc `git rebase -i` cho nhiều commit).
- *Đã push rồi*: đừng viết lại lịch sử nhánh chung; ghi mã ở tiêu đề MR / tên nhánh (MR vẫn nối
  được issue), và commit tiếp theo ghi `Refs: KEY`. Commit cũ sẽ nằm ở tab "Chưa gắn issue" với ghi
  chú "Trong MR đã gắn issue".
- *Commit có mã mà không hiện trong issue*: mã viết thường; dự án Jira chưa đăng ký; issue chưa đồng
  bộ về (đợi chu kỳ hoặc Admin › Jira › Đồng bộ ngay); đó là merge commit; hoặc repo thuộc dự án bạn
  không được xem.
- *Bug đã sửa mà vẫn ở "Bug chưa có bản sửa"*: commit sửa thiếu dòng `Fixes: KEY` và tiêu đề không
  phải `fix(...)` ghi mã; hoặc MR chỉ nhắc mã trong mô tả. Thêm `Fixes:` ở commit sau, hoặc đưa mã
  vào tiêu đề MR / tên nhánh.
- *Một commit sửa nhiều bug*: `Fixes: CRM-1, CRM-2` (nên tách commit nếu được).
"""

TASK_JIRA = """
Màn **Task Jira** (/jira, menu Theo dõi; hiện khi Jira đã kết nối) — chỉ đọc từ Jira.
- KPI trong kỳ: issue làm trong kỳ, đang mở, xong trong kỳ, có code (MR/commit), bug đã có bản sửa,
  có Story Points, còn lỗi nặng (critical/high mở trong review của MR).
- Lọc: tìm mã/tiêu đề, dự án, dự án Jira, trạng thái, loại, người làm (có "Tôi"), Code (đã có / chưa
  có MR-commit, bug chưa có bản sửa); thời gian theo bộ lọc thời gian chung. Bộ lọc nằm trên URL nên
  gửi link được.
- Bấm mã issue: chi tiết (link Jira, người làm, hạn, component/label, issue cha và việc con, mọi MR và
  commit ghi mã cùng review, điểm, critical/high). Nút ↗ cạnh mã mở thẳng Jira.
- Tab "Chưa gắn issue": MR hoặc commit trong kỳ không ghi mã; tỉ lệ commit có mã.
- Lối vào khác: số issue ở Admin › Jira mở danh sách của dự án Jira đó; dải Jira trên trang review
  có "Xem task".
- Ai thấy gì: issue của dự án mình được xem (admin, quyền jira.admin: tất cả); điểm/finding review
  chỉ hiện khi được xem review đó.
- **Liên kết commit bằng tay**: trong chi tiết issue, mục Commit › "Liên kết commit": gõ SHA (vd.
  3f2a9c1) hoặc nội dung commit, chọn "Sửa issue" (tính vào "Bug đã có bản sửa") hoặc "Liên quan".
  Chỉ commit đã review, thuộc dự án của issue; một issue liên kết được nhiều commit. Thành viên liên
  kết được commit của chính mình; manager dự án, jira.admin, admin liên kết được mọi commit. Liên kết
  tay có nhãn "Liên kết tay", gỡ được ("Gỡ liên kết"); liên kết đọc từ message thì không gỡ tay.
- **Review lại**: nút trên mỗi commit đã review (người có quyền chạy lại review của dự án) — chạy một
  lượt review mới để xem bản sửa đã ổn chưa; kết quả hiện lại trong issue khi xong.
- **Chuyển trạng thái trên Jira**: manager của dự án, jira.admin, admin thấy nút "Chuyển trạng thái"
  cạnh trạng thái issue: chọn một bước Jira cho phép (dấu ✓ là trạng thái hoàn thành), ghi chú tuỳ
  chọn; Jira nhận thêm bình luận "[CodePulse] tên người: ghi chú". Cần tài khoản dịch vụ Jira có quyền
  Transition issues trên dự án đó, nếu không sẽ báo lỗi.
"""

LLM_ACCOUNTS = """
**Nhiều tài khoản Claude** (Admin › LLM & Chi phí › Kết nối Claude; quyền admin.llm).
- Review luôn chạy bằng tài khoản **mặc định**; chỉ khi nó lỗi đăng nhập, hết hạn mức gói hoặc gói
  không có model review mới chuyển sang dự phòng theo thứ tự xếp; token trong .env là dự phòng cuối.
- Quay về mặc định tự động ở lần gọi thật đầu tiên sau giờ thử lại (không ping định kỳ): hết hạn
  mức có giờ reset → đúng giờ đó; không có → sau "Thử lại tài khoản Claude bị lỗi sau (phút)" ở
  Admin › Cấu hình (mặc định 15), gấp đôi mỗi lần còn lỗi, tối đa 8 lần; lỗi đăng nhập → 8 lần
  chu kỳ (đăng nhập lại hoặc bấm Kiểm tra là dùng lại ngay).
- HTTP 429: đọc nội dung — có giờ reset hoặc lời về hạn mức gói thì chuyển ngay; giới hạn theo phút
  thì thử lại; 429 không rõ loại thử lại 1 lần, lặp lại thì coi là hết hạn mức.
- Lỗi máy chủ Anthropic / quá tải / timeout không chuyển tài khoản.
- Ngữ cảnh review giữ nguyên khi đổi tài khoản: mỗi lần gọi độc lập, cùng một model ghim sẵn
  (không bao giờ tự hạ model); trang review ghi tài khoản đã trả lời.
- Thao tác: thêm (Đăng nhập Claude hoặc dán token sk-ant-oat…/API key), Đặt mặc định, ↑↓ thứ tự,
  Đăng nhập lại, Kiểm tra, Tắt/Bật, Đổi tên, Xoá.
"""

TIME_SCOPE = """
**Bộ lọc thời gian dùng chung**: chọn thời gian ở một màn (Tổng quan, Insights, Dự án, Team, Thành
viên, Báo cáo thành viên, Tích hợp MCP, DPI, Minh chứng, Task Jira) thì các màn khác mở với đúng
thời gian đó; sửa ở đâu các màn sau theo đó. Lưu trên trình duyệt (các tab khác theo luôn), hết hạn
sau 12 giờ không chọn lại. Màn chỉ có tuần/tháng (Dự án, Team) hiện tuần/tháng chứa ngày cuối của
khoảng ngày, có dòng ghi chú. Critical Board và Đóng finding không theo (là hàng đợi việc).
"""

QUEUE = """
**Thứ tự chạy review**: yêu cầu của member chạy trước review tự động: /review qua Telegram, "Chạy lại"
một review, kiểm AI của yêu cầu đóng finding (web/MCP) và của khiếu nại. Không ưu tiên: lô commit
gửi từ trang "Yêu cầu review" sau khi quét danh sách, webhook GitLab, backfill, chấm lại tiêu chí.
Khi LLM đang bị đánh dấu lỗi, yêu cầu của member chạy ngay nếu đã có tài khoản Claude dùng được.
"""

TOPICS: tuple[Topic, ...] = (
    Topic(
        "jira-standard",
        "Chuẩn liên kết Jira ↔ nhánh, commit, MR (commit sửa bug, hook, CI)",
        (
            "commit",
            "nhanh",
            "branch",
            "fixes",
            "refs",
            "hook",
            "ma jira",
            "key",
            "lien ket",
            "link",
            "noi issue",
            "gan issue",
            "chua gan",
            "ban sua",
            "sua bug",
            "fix bug",
            "conventional",
            "push rule",
            "gitlab ci",
            "chuan",
            "quy uoc",
            "tieu chuan",
        ),
        ("/jira", "/guide/jira"),
        JIRA_STANDARD,
    ),
    Topic(
        "task-jira",
        "Màn Task Jira: tra cứu issue, MR, commit, review",
        ("task jira", "jira", "issue", "story point", "bug"),
        ("/jira", "/admin"),
        TASK_JIRA,
    ),
    Topic(
        "llm-accounts",
        "Nhiều tài khoản Claude: mặc định, dự phòng, tự chuyển",
        ("tai khoan claude", "claude", "du phong", "token", "han muc", "429", "limit", "llm"),
        ("/admin",),
        LLM_ACCOUNTS,
    ),
    Topic(
        "time-scope",
        "Bộ lọc thời gian dùng chung giữa các màn",
        ("thoi gian", "khoang ngay", "bo loc", "dong bo", "tuan", "thang"),
        ("/overview", "/insights", "/members", "/teams", "/projects", "/mcp-report", "/dpi"),
        TIME_SCOPE,
    ),
    Topic(
        "queue",
        "Thứ tự chạy review và ưu tiên yêu cầu của member",
        ("uu tien", "hang doi", "cho review", "queue", "cham", "lau", "yeu cau review"),
        ("/reviews",),
        QUEUE,
    ),
)


def _plain(text: str) -> str:
    """Lower case without Vietnamese accents (đ → d), for keyword matching."""
    folded = unicodedata.normalize("NFD", text.casefold().replace("đ", "d"))
    return "".join(c for c in folded if unicodedata.category(c) != "Mn")


def _has(haystack: str, word: str) -> bool:
    return re.search(rf"(?<![a-z0-9]){re.escape(word)}(?![a-z0-9])", haystack) is not None


def pick(question: str, path: str | None = None) -> list[Topic]:
    """The topics a question (by keywords, more hits first) or the screen on view matches."""
    asked = _plain(question)
    scored: list[tuple[int, int, Topic]] = []
    for order, topic in enumerate(TOPICS):
        hits = sum(1 for k in topic.keywords if _has(asked, k))
        on_screen = bool(path) and any((path or "").startswith(p) for p in topic.paths)
        if hits or on_screen:
            scored.append((hits * 2 + (1 if on_screen else 0), -order, topic))
    scored.sort(key=lambda s: (s[0], s[1]), reverse=True)
    return [t for _, _, t in scored[:MAX_TOPICS]]


def howto_block(question: str, path: str | None = None) -> str:
    """The <huong_dan> text of a turn: the matching topics in full, and every topic's title."""
    chosen = pick(question, path)
    lines = ["Các chủ đề hướng dẫn có sẵn: " + "; ".join(t.title for t in TOPICS) + "."]
    for topic in chosen:
        lines += ["", f"## {topic.title}", topic.body.strip()]
    if not chosen:
        lines += ["", "(Câu hỏi này không khớp chủ đề hướng dẫn nào.)"]
    return "\n".join(lines)
