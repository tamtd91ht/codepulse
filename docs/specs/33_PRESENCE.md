# 33 – Trạng thái online (Presence)

> Yêu cầu của người dùng (2026-11-23): một màn hình giám sát user / member đang online hay
> offline, thời lượng online và offline là bao nhiêu; một member có thể đăng nhập ở nhiều tab và
> nhiều thiết bị. Phương án được duyệt trước khi code; người dùng chọn **WebSocket** làm kênh
> heartbeat. Hướng tiếp cận chung, dùng lại được cho dự án khác: `docs/PRESENCE_PATTERN.md`.

## 1. Mục tiêu

- Biết **bây giờ** mỗi người đang Online, Tạm vắng hay Offline, và từ lúc nào.
- Biết **trong một ngày** họ online bao lâu, tạm vắng bao lâu, offline bao lâu.
- Đúng khi một người mở nhiều tab, nhiều trình duyệt, nhiều thiết bị.
- Đúng khi không ai báo "tôi rời đi": tab crash, máy sleep, mất mạng.

Không phải mục tiêu: chấm công, đo năng suất, theo dõi người dùng đang xem trang nào.

## 2. Ba trạng thái

| Trạng thái | Nghĩa |
|---|---|
| **Online** | Có ít nhất một tab đang mở **và** có thao tác (chuột, phím, cuộn, chạm) trong 5 phút gần nhất |
| **Tạm vắng** (`away`) | Còn tab mở nhưng không tab nào có thao tác trong 5 phút |
| **Offline** | Không còn tab nào mở |

Presence thuộc về **kết nối (tab)**. Trạng thái của một người là giá trị cao nhất trong các tab
còn sống của họ, và luôn được **tính lại từ danh sách tab**, không dùng bộ đếm tăng giảm.

## 3. Luồng

```
Tab ──WebSocket /api/v1/presence/ws──▶ API ──▶ Redis: tab nào còn sống, có đang thao tác
     gửi {active} khi đổi                       │
                                     trạng thái gộp đổi? ── không ──▶ không đụng DB
                                                │ có
                                                ▼
                                     presence_segments: đóng đoạn cũ, mở đoạn mới
                                                ▲
Scheduler: presence-sweep mỗi 15 giây ──────────┘ tab chết không báo, mốc an toàn, đoạn mồ côi
```

### 3.1 Kênh heartbeat: WebSocket

- Mỗi tab đã đăng nhập mở một socket `GET /api/v1/presence/ws?tab=<id>&device=<id>`
  (`dashboards/web/lib/presence.ts`, gắn một lần ở `AppShell`).
- Phiên được kiểm **một lần** lúc bắt tay (cookie `codepulse_session`; `SameSite=Lax` nên trang
  của site khác không mở được socket thay người dùng). Sau đó cứ 5 phút kiểm lại phiên và trạng
  thái tài khoản; hết hạn thì đóng với mã `4401`.
- Server tự làm mới bản ghi của tab mỗi 30 giây chừng nào socket còn mở. Trình duyệt chỉ gửi
  `{"active": true|false}` **khi đổi**. Nhịp sống không phụ thuộc timer JavaScript, nên tab nền
  bị trình duyệt bóp timer vẫn được tính đúng.
- Socket đóng (đóng tab, mất mạng, uvicorn phát hiện qua ping/pong) thì tab bị gỡ ngay: không
  phải chờ hết hạn.
- Server trả `{"ok":true}` sau khi ghi nhận; trình duyệt dựa vào đó để biết socket thông suốt.
- Từ chối = **accept rồi đóng kèm mã**: `4401` chưa đăng nhập / hết phiên, `4400` sai `tab` / `device`,
  `4429` quá 30 socket của một người trong một tiến trình. Đóng trước khi accept thì trình duyệt chỉ
  thấy bắt tay hỏng (1006), không phân biệt được với "proxy không có WebSocket" và sẽ thử lại mãi.
  Nhận ba mã này trình duyệt dừng hẳn.
- Đổi `active` được ghi tối đa 2 giây một lần cho mỗi socket (lấy giá trị cuối), nên một tab bật tắt
  liên tục không sinh một dòng lịch sử cho mỗi thông điệp. Lần làm mới đầu lệch ngẫu nhiên 15–30
  giây để các socket của một server vừa khởi động lại không dồn vào cùng một giây.
- Redis không phản hồi: presence nghỉ 30 giây rồi mới thử lại (`PAUSE_S`). Mỗi lần làm mới chạy trong
  thread pool dùng chung; nếu mọi socket cùng chờ timeout thì các request thường sẽ bị nghẽn.

Vì sao socket thay vì gọi API định kỳ: mỗi request API ở CodePulse nạp lại toàn bộ quyền của
người gọi (nhiều truy vấn), còn socket xác thực một lần; và socket cho biết tab rời đi tức thì.

### 3.2 Dự phòng: HTTP

Trình duyệt mở socket thất bại 2 lần liên tiếp (reverse proxy không chuyển `Upgrade`) thì gọi
`POST /api/v1/presence/beat {tab, device, active}` mỗi 30 giây và `POST /presence/leave {tab}` khi
rời trang, đồng thời vẫn thử lại socket. Hai kênh ghi vào cùng một lõi (`app/presence`).

Rewrite `/api` của dashboard (Next 16) chuyển được WebSocket upgrade (đã kiểm 2026-11-23, cả
`next start` lẫn bản standalone). Reverse proxy đặt trước dashboard phải chuyển tiếp `Upgrade` và
`Connection` (RUNBOOK).

### 3.3 Định danh

| | Lưu ở | Dùng để |
|---|---|---|
| `tab` | `sessionStorage` (`cp_tab`) | đếm số tab |
| `device` | `localStorage` (`cp_device`) | đếm số thiết bị (các tab cùng trình duyệt dùng chung) |
| mã kết nối | server: `<tab>.<ngẫu nhiên>` (HTTP: `<tab>.http`) | tab kết nối lại không bị socket cũ gỡ nhầm |

Loại thiết bị (`desktop` / `mobile`) suy từ User-Agent, chỉ nằm trong Redis.

## 4. Dữ liệu

### 4.1 Trạng thái sống (Redis)

| Khoá | Kiểu | Nội dung |
|---|---|---|
| `cp:presence:tabs:{user}` | HASH | mã kết nối → `{t, a, d, k, b, c}`: lần làm mới cuối, đang thao tác, device, loại, lúc xuất hiện, các lần `a` đổi trong 10 phút gần nhất |
| `cp:presence:live` | ZSET | user → lần làm mới cuối; sweeper duyệt ở đây, không quét khoá |
| `cp:presence:state` | HASH | user → trạng thái đã ghi vào lịch sử, để biết có **đổi** hay không |

| `cp:presence:lock:{user}` | khoá | một lần đọc-sửa-ghi các tab của một người tại một thời điểm (tự nhả sau 15 giây) |

Tab quá `TAB_TTL_S` = 100 giây không được làm mới thì coi là chết. Tối đa 30 tab mỗi người.

Khoá theo người là bắt buộc: khi F5, socket cũ đóng và socket mới mở gần như cùng lúc ở hai luồng
(hoặc hai tiến trình); không có khoá thì lời "rời đi" của socket cũ, đọc danh sách tab trước khi
socket mới ghi vào, sẽ xoá luôn kết nối mới.

### 4.2 Lịch sử: `presence_segments`

| cột | ý nghĩa |
|---|---|
| id, user_id | `user_id` xoá theo người (CASCADE) |
| state | `online` · `away` |
| started_at, ended_at | khoảng `[bắt đầu, kết thúc)`; `ended_at` NULL = đoạn đang mở |
| last_seen_at | mốc an toàn, sweeper cập nhật tối đa mỗi phút |

- Chỉ ghi khi trạng thái gộp **đổi**; không ghi theo nhịp tim. Offline không lưu: là khoảng trống.
- Chỉ mục duy nhất một phần `uq_presence_segments_open (user_id) WHERE ended_at IS NULL`: mỗi
  người tối đa một đoạn đang mở.
- `transition(user, state, lúc)` là thao tác ghi duy nhất, lặp lại được, khoá theo người bằng
  `pg_advisory_xact_lock`. Đoạn dài 0 giây bị xoá. Các đoạn của một người **không bao giờ chồng
  nhau**: thay đổi bị ghi lùi về trước lúc đoạn gần nhất kết thúc thì bắt đầu tại lúc đó (các truy
  vấn đọc dựa vào bất biến này).

## 5. Sweeper (`presence-sweep`, scheduler, 15 giây)

1. Với mỗi người trong `cp:presence:live`: gỡ tab hết hạn và **dựng lại diễn biến** từ lần làm mới
   cuối của tab chết tới bây giờ, coi như tab đó rời đi đúng lúc nó im lặng (`_replay`). Mỗi kết
   nối nhớ lúc nó xuất hiện (`b`) và các lần `active` đổi gần đây (`c`), đủ để biết các tab còn lại đã
   làm gì trong khoảng đó. Ví dụ: điện thoại im từ 12:00, máy bàn dùng tới 12:01 rồi để yên; khi
   bản ghi của điện thoại hết hạn lúc 12:01:40, lịch sử ghi Online tới 12:01, Tạm vắng từ 12:01
   (không phải từ 12:00, cũng không phải từ lúc quét).
2. Mỗi 60 giây: cập nhật `last_seen_at` cho đoạn đang mở của người còn sống.
3. Đoạn đang mở mà người đó không còn trong Redis và mốc an toàn cũ hơn 100 giây: đóng tại
   `last_seen_at` (Redis bị xoá, API bị kill).
4. Người còn sống mà không có đoạn đang mở: xoá trạng thái đã ghi để lần làm mới kế tiếp ghi lại.

Lỗi DB của một người không dừng lượt quét của những người khác; người đã bị xoá khỏi hệ thống thì bị
gỡ khỏi Redis.

Sweeper chỉ lo phần socket không tự báo được (tiến trình API sập, tab đi đường HTTP). Redis
không phản hồi thì bỏ qua và ghi log mỗi phút một lần.

## 6. Thời lượng

Tính theo ngày của `REPORT_TZ`; đoạn vắt qua nửa đêm được chia cho hai ngày khi tính.

| Số | Cách tính |
|---|---|
| Online, Tạm vắng | tổng phần giao của các đoạn với ngày; đoạn đang mở tính tới bây giờ |
| Bắt đầu, Lần cuối | đầu đoạn sớm nhất, cuối đoạn muộn nhất trong ngày |
| **Offline** | `(lần cuối − bắt đầu) − online − tạm vắng`: các khoảng trống **giữa** lần đầu và lần cuối online trong ngày |
| Số phiên | số chuỗi đoạn liền nhau; khoảng trống ≤ 10 giây (F5 trang) không tách phiên |

Ở cột "Hiện tại", người offline hiện "từ HH:MM · đã bao lâu" tính từ lần cuối thấy.

## 7. API

| | |
|---|---|
| `WS /presence/ws?tab&device` | kênh heartbeat (§3.1) |
| `POST /presence/beat`, `POST /presence/leave` | dự phòng (§3.2); token `cpat_` không dùng được |
| `GET /presence?day=` | mọi người mình được xem: trạng thái hiện tại, từ lúc nào, số tab / thiết bị, tổng của `day` (mặc định hôm nay), số đếm theo trạng thái, danh sách team để lọc |
| `GET /presence/users/{id}?from=&to=` | các ngày của một người (tối đa 62 ngày, mặc định 7): tổng và các đoạn để vẽ timeline |

Trạng thái hiện tại đọc thẳng từ Redis (đúng cả khi sweeper chậm). Redis không phản hồi thì trả
`live: false` và trạng thái lấy theo lịch sử.

Màn hình gọi `GET /presence` 10 giây một lần, nên hai truy vấn lịch sử chỉ đi theo chỉ mục
`(user_id, started_at)`: các đoạn bắt đầu trong ngày cộng một đoạn bắt đầu gần nhất trước ngày đó cho
mỗi người (`segments_between`); "lần cuối thấy" chỉ hỏi cho người đang offline, mỗi người một lần dò
chỉ mục (`last_seen`). Không truy vấn nào quét cả lịch sử 180 ngày.

## 8. Quyền: `presence.view`

Quyền có phạm vi, nhạy cảm (ghi audit khi cấp), nhóm "Trạng thái online".

| Phạm vi | Thấy ai | Bộ quyền mặc định |
|---|---|---|
| `own` | chính mình | Thành viên |
| `team` | người trong team mình lead hoặc manage (cả team con) | Lead, Manager |
| `all` | mọi người đang hoạt động | Admin; hoặc bộ quyền admin cấp |

Người ngoài phạm vi: không có trong danh sách, và `GET /presence/users/{id}` trả 404. Ở phạm vi
`own` / `team`, tên team của mỗi người chỉ gồm các team mà người xem thuộc về, lead hoặc manage.

## 9. Màn hình `/presence` (menu "Trạng thái online")

- Dòng thông báo minh bạch: hệ thống ghi nhận gì, giữ bao lâu, không dùng để chấm điểm.
- Dải số: Online / Tạm vắng / Offline / Tổng (ẩn khi chỉ xem của mình).
- Bộ lọc: tìm tên, Team (MultiSelect), Trạng thái, ngày tính thời lượng.
- Bảng: Thành viên · Hiện tại (chấm màu, từ bao lâu) · Online · Tạm vắng · Offline · Bắt đầu ·
  Lần cuối · Đang mở (số thiết bị, số tab) · nút lịch sử (icon, cột cuối).
- Lịch sử một người (Drawer; với người chỉ xem của mình thì hiện ngay trên trang): chọn khoảng
  ngày (7 / 14 / 30 ngày), tổng của khoảng, mỗi ngày một thanh 24 giờ tô Online / Tạm vắng.
- Tự làm mới 10 giây một lần.

## 10. Quyền riêng tư

- Chỉ ghi **thời gian** và một bit "có thao tác trong 5 phút qua". Không ghi trang đang xem, toạ
  độ chuột, nội dung gõ, địa chỉ IP. Loại thiết bị không vào lịch sử.
- Mỗi người xem được số liệu của chính mình; màn hình nói rõ ai xem được.
- Giữ 180 ngày (`app.presence.KEEP_DAYS`), job `retention-purge` xoá.
- **Không** đưa vào DPI, điểm review hay bất kỳ điểm số nào.

## 11. Quyết định (người dùng duyệt 2026-11-23: "làm theo đề xuất")

- [x] **D1 Ai được xem**: quyền `presence.view`; admin tất cả, lead / manager team mình, member
  của chính mình; có dòng thông báo minh bạch trên màn hình.
- [x] **D2 Có trạng thái Tạm vắng**: có, ngưỡng 5 phút không thao tác.
- [x] **D3 Định nghĩa offline**: màn trực tiếp là "offline từ lúc nào"; theo ngày là tổng khoảng
  trống giữa lần đầu và lần cuối online trong ngày (không cần khai giờ làm, ngày nghỉ).
- [x] **D4 Giữ lịch sử**: 180 ngày.
- [x] **D5 DPI**: không dùng.
- [x] **D6 Kênh heartbeat**: WebSocket (người dùng chọn), HTTP làm dự phòng. Lý do giữ dự
  phòng: reverse proxy ngoài cùng nằm ngoài repo, nếu nó chặn upgrade thì mọi người sẽ hiện
  offline.
- [x] **D7 Làm mới màn giám sát**: polling 10 giây; đẩy qua SSE khi cần tức thì (IDEAS).

## 12. Giới hạn đã biết

- Trình duyệt đóng băng hoặc huỷ tab nền để tiết kiệm bộ nhớ thì tab đó được tính là đã đóng.
- Nhân đôi tab (Duplicate) sao chép `sessionStorage`, nên hai tab đó được đếm là một tab; trạng
  thái vẫn đúng.
- Giới hạn 30 socket mỗi người đếm theo tiến trình API; chạy nhiều tiến trình thì trần thực tế là
  30 × số tiến trình (bản ghi trong Redis vẫn tối đa 30).
- Khi tiến trình API bị kill, các tab của nó được ghi là rời đi tại lần làm mới cuối (sai số tối
  đa 30 giây), sau khi sweeper thấy chúng hết hạn (tối đa 115 giây); thường trình duyệt đã kết
  nối lại trước đó.

## 13. Kiểm thử

`tests/api/test_presence.py`: nhiều tab gộp một trạng thái; Tạm vắng và quay lại; làm mới cùng
trạng thái không đụng DB; tab kết nối lại; `transition` lặp lại được; tab im lặng rời đi tại lần
nói cuối; tab đang dùng chết còn tab vắng; mốc an toàn và đoạn mồ côi; giới hạn số tab; xoá theo
hạn lưu; thời lượng và vắt qua nửa đêm; phạm vi own / team / all; socket (online, away, đóng,
từ chối bằng mã đóng khi chưa đăng nhập / sai id / quá số socket); kênh HTTP dự phòng; Redis sập thì
nghỉ chứ không chờ từng lần; các đoạn không chồng nhau; truy vấn theo cửa sổ ngày; một người lỗi
không dừng sweeper.

Đã kiểm tay trên stack thật (Postgres, Redis, uvicorn, bản standalone của dashboard, Chrome headless)
ngày 2026-11-23: 3 tab trên 2 trình duyệt là một người online; đóng tab giảm số đếm ngay; đóng hết là
offline ngay; `{active:false}` thành Tạm vắng; F5 năm lần vẫn online, một phiên; socket không cookie
nhận mã 4401 qua proxy; kill API thì sweeper gỡ kết nối sau khoảng 100 giây.
