# Theo dõi trạng thái online của người dùng (Presence) – giải pháp dùng lại được

> Tài liệu này không gắn với CodePulse. Nó ghi lại **bài toán, hướng tiếp cận và các quyết định**
> để dự án nào cần biết "ai đang online, online bao lâu, offline bao lâu" cũng áp dụng được.
> Bản áp dụng cụ thể cho CodePulse nằm ở `docs/specs/33_PRESENCE.md`.

## 1. Bài toán

Cần trả lời ba câu hỏi:

1. **Bây giờ** người này đang online, tạm vắng hay offline? Từ lúc nào?
2. **Trong một khoảng thời gian** (hôm nay, tuần này) họ online bao lâu, vắng bao lâu, offline bao lâu?
3. Họ đang mở ứng dụng trên **mấy thiết bị, mấy tab**?

Ba điều làm bài toán khó hơn vẻ ngoài:

- **Một người có nhiều kết nối**: nhiều tab, nhiều trình duyệt, nhiều thiết bị. Đóng một tab không
  có nghĩa là offline.
- **Không có sự kiện "rời đi" đáng tin**: tab bị crash, máy sleep, mất mạng, điện thoại đưa trình
  duyệt xuống nền. Server không được báo gì cả.
- **Mở tab không có nghĩa là đang dùng**: tab để đó cả ngày vẫn "kết nối".

## 2. Cách các hệ thống lớn làm

| Hệ thống | Cách làm | Bài học |
|---|---|---|
| Slack, Microsoft Teams | Active khi client còn kết nối **và** có tương tác; không tương tác vài phút thì thành Away | Cần ba trạng thái, không phải hai |
| Discord | Mỗi kết nối tự gửi heartbeat; trạng thái người dùng là **gộp** của mọi phiên | Presence tính theo kết nối, không theo người |
| Phoenix Presence, Firebase Realtime DB | Mỗi kết nối một khoá **tự hết hạn**; người dùng online khi còn ít nhất một khoá | Offline là "hết hạn", không phải "được báo" |
| Facebook, Zalo, WhatsApp | "Hoạt động 5 phút trước" | Lưu thời điểm thấy lần cuối |

Bốn hệ thống, một mẫu chung. Phần còn lại của tài liệu là mẫu đó.

## 3. Bốn nguyên tắc

1. **Presence thuộc về kết nối (tab), không thuộc về người.** Mỗi tab tự báo danh. Trạng thái của
   người dùng là giá trị cao nhất trong các tab còn sống:
   `online` (có tab đang thao tác) > `away` (còn tab, không tab nào thao tác) > `offline` (hết tab).
2. **Offline là hệ quả của im lặng.** Tab nào quá hạn không báo danh thì coi như chết. Sự kiện
   "rời đi" (đóng tab, đăng xuất) chỉ là đường tắt để offline nhanh hơn, không bao giờ là điều kiện.
3. **Tách trạng thái sống khỏi lịch sử.** Trạng thái sống thay đổi liên tục, mất cũng được, nên để
   ở kho nhanh có hết hạn (Redis). Lịch sử cần bền và truy vấn được, nên để ở cơ sở dữ liệu và
   **chỉ ghi khi trạng thái đổi**.
4. **Lưu khoảng thời gian, không lưu nhịp tim.** Lịch sử là các đoạn `[bắt đầu, kết thúc)` kèm
   trạng thái. Offline là khoảng trống giữa các đoạn, không cần lưu.

## 4. Kiến trúc

```
  Tab A ─┐  heartbeat: socket hoặc API (mục 5)
  Tab B ─┼────────────▶  API  ──▶  Kho sống (Redis, tự hết hạn)
  Mobile ┘  {tab, device, active}   │  tab nào còn sống, có đang thao tác
                                    │
                    trạng thái gộp đổi?  ── không ──▶ xong (không đụng DB)
                                    │ có
                                    ▼
                         Lịch sử (DB): đóng đoạn cũ, mở đoạn mới
                                    ▲
  Sweeper 15s ──────────────────────┘  phát hiện tab chết vì im lặng,
                                       ghi mốc an toàn, dọn đoạn mồ côi
```

Ba thành phần, mỗi thành phần một việc:

- **Beat** (client → API): "tab này còn sống, người dùng có/không đang thao tác".
- **Sweeper** (tiến trình nền): thay mặt những tab không còn nói được nữa.
- **Màn giám sát** (đọc): trạng thái sống lấy từ kho sống, thời lượng lấy từ lịch sử.

## 5. Kênh heartbeat: socket hay API

Cả hai đều chạy được trên cùng một lõi (mục 6 trở đi không đổi). Khác nhau ở chỗ **ai giữ nhịp**:

| | WebSocket | Gọi API định kỳ |
|---|---|---|
| Ai giữ nhịp | Server: socket còn mở thì server tự làm mới bản ghi của tab | Trình duyệt: `POST /presence/beat` mỗi 30 giây |
| Biết tab rời đi | Ngay khi socket đứt (kể cả mất mạng, nhờ ping/pong ở tầng giao thức) | Khi nhận `leave`, hoặc sau hạn sống |
| Xác thực | Một lần lúc bắt tay, kiểm lại định kỳ | Mỗi request. Đắt nếu mỗi request phải nạp phiên, quyền từ DB |
| Tab nền bị bóp timer | Không ảnh hưởng: nhịp sống không dựa vào timer JavaScript | Beat thưa xuống khoảng 1 lần/phút, hạn sống phải rộng |
| Hạ tầng | Mọi proxy trên đường đi phải chuyển tiếp `Upgrade`; kết nối giữ lâu | Không cần gì thêm |
| Trạng thái ở server | Vòng lặp theo kết nối trong tiến trình API (nhưng **dữ liệu** presence vẫn ở kho sống) | Không có |

**Chọn socket** khi kiểm soát được đường proxy và bước xác thực mỗi request không rẻ, hoặc đã có
sẵn WebSocket. **Chọn API** khi cần đơn giản tối đa hoặc proxy không cho upgrade. **Tốt nhất là
cả hai**: socket là kênh chính, API là dự phòng tự kích hoạt khi socket không mở được. Chi phí
thêm nhỏ vì cả hai gọi cùng hai hàm `beat` / `leave`.

Dù chọn kênh nào, ba điều sau không đổi:

- **Vẫn cần hạn sống và sweeper.** Socket đứt thì server biết, nhưng khi chính tiến trình server
  chết thì không ai gỡ bản ghi của các socket nó đang giữ.
- **Vẫn lưu theo kết nối trong kho sống**, không đếm socket trong bộ nhớ tiến trình: bộ nhớ mất
  khi khởi động lại và sai khi chạy nhiều tiến trình.
- **Mã kết nối do server sinh** (`<tab>.<ngẫu nhiên>`). Khi một tab kết nối lại, socket cũ đóng
  muộn hơn socket mới mở; nếu cả hai dùng chung một mã thì lời "rời đi" của socket cũ sẽ gỡ nhầm
  kết nối mới.

### Phía client

Gắn **một lần** ở khung ứng dụng (app shell), không gắn theo từng màn hình.

| Việc | Cách làm | Lý do |
|---|---|---|
| Định danh tab | `tab_id` ngẫu nhiên trong `sessionStorage` | Sống theo tab, F5 vẫn giữ |
| Định danh thiết bị | `device_id` ngẫu nhiên trong `localStorage` | Các tab cùng trình duyệt dùng chung |
| Đang thao tác? | Ghi thời điểm chuột / phím / cuộn / chạm cuối; `active = (bây giờ - lần cuối) < 5 phút` | Phân biệt Online với Away |
| Báo trạng thái | Chỉ gửi `{active}` **khi đổi** (socket), hoặc kèm trong mỗi beat (API) | Ít lưu lượng, đổi trạng thái tức thì |
| Xác nhận kênh | Server trả một thông điệp sau khi ghi nhận; client coi socket là "thông" khi nhận được | Phát hiện proxy cho mở socket nhưng không chuyển dữ liệu |
| Kết nối lại | Lùi dần 1 → 60 giây; kết nối ngay khi tab hiện lại, mạng có lại, trang ra khỏi bfcache | Không dội server khi nó vừa khởi động lại |
| Chuyển dự phòng | Sau 2 lần socket không thông: beat qua API, vẫn thử lại socket; socket thông thì dừng API và gửi `leave` cho kết nối API | Không bao giờ mất presence vì hạ tầng |
| Dừng hẳn | Server đóng socket với mã "hết phiên" hoặc API trả 401 | Không kết nối lại vô hạn sau khi đăng xuất |

Phía server của socket, bốn điều dễ sót:

- **Từ chối bằng cách accept rồi đóng kèm mã** (4401…). Đóng trước khi accept thì trình duyệt chỉ thấy
  bắt tay hỏng (mã 1006), không đọc được lý do, và client sẽ thử lại mãi. Thư viện test thường vẫn
  cho thấy mã, nên lỗi này chỉ lộ trên trình duyệt thật.
- **Giới hạn tần suất đổi `active`** (ví dụ 2 giây một lần, lấy giá trị cuối) và **trần số socket mỗi
  người**: mỗi lần đổi là một giao dịch ghi lịch sử.
- **Kiểm lại phiên theo đồng hồ riêng**, không gắn vào nhánh "hết giờ chờ thông điệp": client gửi liên
  tục sẽ làm nhánh đó không bao giờ chạy.
- **Ngắt mạch khi kho sống chết.** Nếu mỗi lần làm mới là một lời gọi chặn trong thread pool dùng
  chung, một nghìn socket cùng chờ timeout sẽ làm nghẽn mọi request khác. Sau một lỗi, nghỉ vài chục
  giây rồi mới thử lại. Làm lệch ngẫu nhiên nhịp đầu để các socket không dồn vào một giây.

Lưu ý khi viết client:

- Nghe sự kiện thao tác ở chế độ `passive` và chỉ ghi một con số thời gian. Không gửi gì theo
  từng thao tác.
- Dùng `pagehide`, không dùng `unload`/`beforeunload` (không chạy trên di động, phá bfcache).
- Mọi lỗi đều bỏ qua. Presence không bao giờ được làm hỏng trải nghiệm chính.
- Không cần đồng bộ giữa các tab (BroadcastChannel). Server gộp là đủ và đơn giản hơn.
- Cookie phiên nên là `SameSite=Lax` hoặc `Strict`: bắt tay WebSocket không bị chặn bởi CORS, nên
  đây là thứ ngăn trang của site khác mở socket thay người dùng. Nếu không thể, kiểm tra header
  `Origin` lúc bắt tay.

## 6. Kho sống (Redis)

Ba khoá, không cần Lua, không cần khoá phân tán:

| Khoá | Kiểu | Nội dung |
|---|---|---|
| `presence:tabs:{user}` | HASH | mã kết nối → `{t: lần beat cuối, a: đang thao tác, d: device_id, k: loại thiết bị, b: lúc xuất hiện, c: các lần a đổi gần đây}`; cả khoá tự hết hạn |
| `presence:live` | ZSET | `user` → lần beat cuối bất kỳ; để sweeper liệt kê người còn sống mà không phải quét khoá |
| `presence:state` | HASH | `user` → trạng thái gộp đã ghi vào lịch sử (`online` / `away`); để biết có **đổi** hay không |
| `presence:lock:{user}` | khoá có hạn | một lần đọc-sửa-ghi các kết nối của một người tại một thời điểm |

Trạng thái gộp của một người, tính ở bất kỳ đâu cần:

```
tabs  = các field của presence:tabs:{user} có t > bây giờ - TAB_TTL     (bỏ và xoá tab chết)
state = "offline" nếu tabs rỗng
        "online"  nếu có tab nào a = 1
        "away"    còn lại
```

Vì trạng thái luôn **tính lại từ dữ liệu gốc** nên không có bộ đếm tăng giảm nào để lệch. (Đếm kết nối bằng `INCR`/`DECR` là cách sai phổ biến nhất:
chỉ cần một lần `DECR` không tới là người dùng online mãi mãi.)

**Vẫn cần một khoá theo người.** Mỗi lần xử lý là đọc danh sách kết nối, ghi lịch sử, rồi ghi lại danh
sách. Khi người dùng F5, kết nối cũ đóng và kết nối mới mở gần như cùng lúc, ở hai luồng hoặc hai tiến
trình. Không có khoá, lời "rời đi" của kết nối cũ (đã đọc danh sách trước khi kết nối mới ghi vào)
thấy "không còn ai" và xoá sạch, kể cả kết nối mới: người dùng hiện offline cho tới lần làm mới kế
tiếp. Dùng khoá Redis có hạn (`SET NX PX`, nhả bằng script), giữ vài mili giây. Lỗi này rất dễ
lọt: test đơn luồng không bắt được, phải thử F5 trên trình duyệt thật.

## 7. Lịch sử (cơ sở dữ liệu)

Một bảng:

```
presence_segments
  id            bigserial
  user_id       → users
  state         'online' | 'away'
  started_at    timestamptz
  ended_at      timestamptz null      -- null: đoạn đang mở
  last_seen_at  timestamptz           -- mốc an toàn, cập nhật tối đa mỗi phút
```

- Chỉ mục `(user_id, started_at)`.
- **Chỉ mục duy nhất một phần** trên `user_id` với điều kiện `ended_at IS NULL`: cơ sở dữ liệu tự
  bảo đảm mỗi người có tối đa một đoạn đang mở. Hai request đua nhau thì một bên thua và bỏ qua.

Một thao tác duy nhất ghi vào bảng, và nó **lặp lại được** (idempotent):

```
transition(user, state_mới, lúc):
    đoạn = đoạn đang mở của user (khoá dòng)
    nếu đoạn.state == state_mới: xong
    nếu có đoạn: đoạn.ended_at = max(lúc, đoạn.started_at)
    nếu state_mới != offline: thêm đoạn (user, state_mới, started_at = lúc)
```

Dung lượng: mỗi người vài chục dòng mỗi ngày, dù họ mở bao nhiêu tab.

Giữ một bất biến: **các đoạn của một người không chồng nhau**. Khi một thay đổi bị ghi lùi về trước lúc
đoạn gần nhất kết thúc, cho đoạn mới bắt đầu tại lúc đó. Nhờ bất biến này, truy vấn "các đoạn chạm
vào một ngày" chỉ cần hai lần đọc theo chỉ mục `(user_id, started_at)`: các đoạn bắt đầu trong ngày,
và **một** đoạn bắt đầu gần nhất trước ngày đó. Viết ngây thơ `started_at < cuối AND ended_at > đầu`
sẽ quét cả lịch sử của mỗi người, ở mỗi lần màn giám sát polling.

## 8. Ba luồng

**Beat** (socket: lúc mở, mỗi 30 giây khi còn mở, và khi client báo đổi `active`; API: mỗi request)

1. Ghi kết nối vào `presence:tabs:{user}`, cập nhật `presence:live`.
2. Tính trạng thái gộp (mục 6).
3. So với `presence:state`. Khác thì gọi `transition(user, state, bây giờ)` rồi cập nhật
   `presence:state`. Giống thì dừng: **không chạm cơ sở dữ liệu**.

**Leave** (socket đóng, hoặc API `leave`): xoá kết nối khỏi hash, rồi làm bước 2 và 3.

Thứ tự ghi: lịch sử trước, kho sống sau. Nếu ghi lịch sử lỗi thì `presence:state` còn giá trị cũ
và lần gọi kế tiếp tự làm lại.

**Sweeper** (mỗi 15 giây):

1. Với mỗi người trong `presence:live`: tính lại trạng thái gộp. Nếu đổi vì tab hết hạn thì gọi
   `transition` với **`lúc` = lần beat cuối của tab vừa chết**, không phải thời điểm quét. Nhờ vậy
   máy sleep lúc 12:00, quét lúc 12:02 vẫn ghi đúng là rời đi lúc 12:00.

   Khi người đó **còn tab khác đang sống**, mốc đó chưa đủ. Trong lúc tab im lặng vẫn còn được tính
   (tới hết hạn sống), mọi thay đổi của các tab khác được xét cùng với nó: điện thoại im từ 12:00,
   máy bàn ngừng thao tác lúc 12:01, nhưng hệ thống vẫn coi là Online vì bản ghi của điện thoại
   còn đó, nên không ghi gì. Khi bản ghi hết hạn, ghi "vắng từ 12:00" là lấy mất một phút online;
   ghi "vắng từ lúc quét" là cho thừa. Cách đúng: mỗi kết nối nhớ thêm **lúc nó xuất hiện** và
   **các lần cờ `active` đổi gần đây** (giữ khoảng 10 phút là đủ, vì chỉ cần nhìn lại trong hạn sống);
   khi một tab chết, dựng lại trạng thái từ lần cuối nó lên tiếng
   tới bây giờ như thể nó rời đi đúng lúc đó, rồi ghi các thay đổi theo thứ tự. Nếu trong khoảng đó
   không còn tab nào thì đó là một khoảng offline thật.
2. Mỗi phút một lần: cập nhật `last_seen_at = bây giờ` cho đoạn đang mở của những người còn sống.
3. Đoạn đang mở mà người đó không còn trong kho sống (Redis bị xoá, API sập giữa chừng): đóng
   tại `last_seen_at`. Đây là lý do có cột đó: sai số khi mọi thứ sập tối đa là một phút.
4. Người còn sống mà không có đoạn đang mở (bị bước 3 đóng nhầm trong lúc đua): xoá
   `presence:state` của họ, lần làm mới kế tiếp sẽ mở lại đoạn.

Sweeper nên chạy ở **một** tiến trình (scheduler). Chạy trùng cũng không sai vì `transition` lặp
lại được, chỉ tốn công.

## 9. Tham số và vì sao

| Tham số | Giá trị | Vì sao |
|---|---|---|
| Nhịp làm mới | 30 giây | Đủ thưa để nhẹ, đủ dày để chịu được mất một nhịp. Với socket, đây cũng là sai số tối đa khi tiến trình server chết |
| Hạn sống của tab | 100 giây | Với kênh API: trình duyệt bóp timer của tab nền xuống khoảng **1 lần/phút**; hạn dưới 60 giây sẽ làm tab nền nhấp nháy offline. 100 giây chịu được một nhịp bị bóp cộng độ trễ mạng. Kênh socket không cần rộng thế, nhưng dùng chung một hạn cho đơn giản |
| Ping/pong của socket | 20 giây + 20 giây chờ | Mặc định của nhiều server (uvicorn); phát hiện đầu bên kia đã chết mà không đóng kết nối |
| Ngưỡng Away | 5 phút không thao tác | Mức phổ biến của các ứng dụng chat công việc (5–10 phút) |
| Nhịp sweeper | 15 giây | Độ trễ tối đa của màn giám sát khi mất kết nối đột ngột là hạn sống + nhịp này |
| Mốc an toàn | 60 giây | Sai số tối đa khi kho sống mất dữ liệu |
| Giữ lịch sử | 180 ngày | Tuỳ chính sách; dọn bằng job hằng ngày |

Quy tắc khi chỉnh: `hạn sống ≥ 60 giây + nhịp làm mới + dự phòng` chừng nào còn kênh API. Đừng hạ
hạn sống để "nhạy hơn"; độ nhạy đến từ socket đứt hoặc `leave`.

## 10. Tính thời lượng

Cho một người và một khoảng `[từ, đến)`:

- **Online** = tổng phần giao của các đoạn `online` với khoảng đó. **Away** tương tự. Đoạn đang mở
  tính tới "bây giờ".
- **Lần đầu / lần cuối thấy** = đầu đoạn sớm nhất / cuối đoạn muộn nhất trong khoảng.
- **Offline** cần một định nghĩa, vì "offline 16 giờ mỗi ngày" là con số vô nghĩa. Ba lựa chọn:

| Định nghĩa | Công thức | Khi nào dùng |
|---|---|---|
| Khoảng trống trong ngày làm việc thực tế | `(lần cuối - lần đầu) - online - away` | Mặc định tốt: không cần cấu hình gì |
| Trong khung giờ cố định | `khung giờ - online - away` trong khung | Khi có giờ làm, ngày nghỉ được khai báo |
| Từ lần cuối thấy | `bây giờ - lần cuối` | Chỉ cho màn hình trực tiếp |

- **Số phiên** = số chuỗi đoạn liền nhau (đoạn sau bắt đầu đúng lúc đoạn trước kết thúc).
- Cắt ngày theo **múi giờ nghiệp vụ**, lưu bằng UTC. Một đoạn vắt qua nửa đêm được chia cho hai
  ngày khi tính, không chia khi lưu.

## 11. Các tình huống phải xử lý đúng

| Tình huống | Điều xảy ra |
|---|---|
| Mở 3 tab, đóng 2 | Vẫn online: còn một tab sống |
| Laptop và điện thoại cùng mở | Một người, hai thiết bị; trạng thái gộp như nhiều tab |
| F5 | Kết nối cũ đóng, kết nối mới mở trong vài trăm mili giây: cần khoá theo người (mục 6). Lịch sử có một khoảng trống cỡ một giây; khi đếm số phiên, gộp các khoảng trống dưới 10 giây |
| Tab ở nền nhiều giờ | Vẫn sống (beat thưa hơn), nhưng không có thao tác nên thành Away |
| Máy sleep, mất mạng, crash | Không có `leave`. Sweeper đóng đoạn tại lần beat cuối |
| Đang thao tác ở tab A, tab B để yên | Online: chỉ cần một tab đang thao tác |
| Trình duyệt đóng băng hoặc huỷ tab nền để tiết kiệm bộ nhớ | Tab ngừng beat nên được coi là đã đóng. Đúng với thực tế: người dùng không nhìn thấy nó |
| Đăng xuất ở một tab | Tab đó gửi `leave`; các tab khác nhận 401 ở beat kế tiếp và tự hết hạn |
| Tài khoản bị khoá | Beat bị từ chối, các tab hết hạn sau hạn sống |
| Redis mất dữ liệu | Sweeper đóng các đoạn mồ côi tại mốc an toàn; beat kế tiếp mở đoạn mới |
| Đồng hồ máy người dùng sai | Không ảnh hưởng: mọi mốc thời gian là giờ server. Client chỉ gửi "có/không đang thao tác" |
| Truy cập bằng API token, bot, tích hợp | Không tính. Presence là của người ngồi trước giao diện |

## 12. Vì sao không chọn cách khác

| Cách | Vấn đề |
|---|---|
| Suy ra từ thời điểm request API cuối | Người đang đọc một trang dài không gửi request nào; job tự động thì gửi liên tục. Không phân biệt được tab, không có Away |
| Đếm socket đang mở trong bộ nhớ tiến trình | Mất khi khởi động lại, sai khi chạy nhiều tiến trình. Socket là kênh tốt (mục 5), nhưng dữ liệu presence phải nằm ở kho sống dùng chung |
| Dùng luôn kết nối SSE có sẵn | SSE một chiều: không mang được tín hiệu "đang thao tác", vẫn phải thêm một kênh gửi lên |
| Bộ đếm `INCR` / `DECR` theo người | Lệch vĩnh viễn khi một lần giảm không tới |
| Ghi mỗi nhịp tim vào cơ sở dữ liệu | Hàng triệu dòng vô ích; tính thời lượng phải đoán khoảng trống giữa các nhịp |
| Chỉ dựa vào sự kiện đóng tab | Không bao giờ tới khi crash, sleep, mất mạng |

## 13. Quyền riêng tư

Dữ liệu này định danh từng người và dễ bị dùng sai, nên phải quyết định trước khi viết code:

- **Ai xem được của ai.** Gợi ý: mỗi người xem của mình, người quản lý trực tiếp xem của nhóm,
  quản trị xem tất cả. Đưa vào hệ thống phân quyền như một quyền nhạy cảm.
- **Minh bạch.** Người dùng phải biết ứng dụng ghi nhận thời gian online, và xem được số liệu của
  chính mình.
- **Thu tối thiểu.** Không lưu toạ độ chuột, nội dung gõ, trang đang xem, địa chỉ IP. Chỉ một bit
  "có thao tác trong 5 phút qua". Loại thiết bị chỉ giữ trong kho sống, không vào lịch sử.
- **Không dùng để chấm điểm.** Thời gian online không phải là thời gian làm việc, càng không phải
  năng suất. Ghi rõ điều này ngay trên màn hình.
- **Có hạn lưu** và có job xoá.

## 14. Mở rộng

- **Tải**: mỗi lần làm mới là vài lệnh Redis, không có truy vấn ghi. 10 000 người, mỗi người 3
  tab là khoảng 1 000 lần mỗi giây: một Redis xử lý thoải mái. Với kênh API, phần nặng thật sự
  thường là bước xác thực của chính API; đó là lý do chính để chọn socket.
- **Số kết nối**: mỗi tab một socket. Kiểm giới hạn file descriptor và giới hạn kết nối của
  proxy; đặt trần số kết nối cho mỗi người (ví dụ 30) để một client lỗi không chiếm hết.
- **Nhiều tiến trình API**: không cần làm gì, vì dữ liệu presence nằm ở kho sống. Khi triển khai
  lại, mọi socket đứt cùng lúc và kết nối lại; client lùi dần để không dội server.
- **Màn giám sát thời gian thực**: bắt đầu bằng polling 10 giây. Khi cần tức thì, cho `transition`
  phát một thông điệp pub/sub và đẩy qua SSE hoặc WebSocket.
- **Báo cáo dài hạn**: khi bảng đoạn lớn, thêm bảng tổng hợp theo ngày do job đêm tính.
- **Trạng thái tự đặt** ("Đừng làm phiền", "Đang họp"): là một lớp riêng do người dùng chọn, hiển
  thị đè lên presence tự động. Đừng trộn vào bảng đoạn.

## 14a. Đăng xuất cưỡng bức đi kèm presence

Màn giám sát thường kéo theo yêu cầu "đá" một tài khoản. Với phiên không trạng thái (JWT):

- Mỗi người một **số phiên** (`session_epoch`); token mang số đó; kiểm ở đúng một hàm mà mọi đường vào
  đều gọi. Đá = tăng số. Đừng so mốc thời gian với `iat`: `iat` chỉ chính xác tới giây.
- Socket đang mở không tự biết phiên đã hết. Ghi thời điểm đá vào kho sống; socket nhớ lúc nó mở và
  bị từ chối ở lần làm mới kế tiếp nếu mở trước lúc đá. Không tốn truy vấn DB nào cho mỗi socket.
- Đóng socket bằng mã "hết phiên" và cho client **chuyển về trang đăng nhập**; đừng để người dùng
  ngồi trước một trang chết.
- "Đá tất cả" nên chừa người đang bấm, và nói rõ điều đó ở hộp xác nhận.
- Nói rõ giới hạn: đăng nhập qua SSO thì "đăng nhập lại" có thể chỉ là một lần bấm nếu phiên ở nhà
  cung cấp danh tính còn sống.

## 15. Danh sách kiểm thử

- [ ] Hai tab beat, một tab `leave`: vẫn online, không sinh đoạn mới.
- [ ] Tab duy nhất `leave`: đoạn đóng đúng lúc đó.
- [ ] Mọi tab báo `active = false`: chuyển Away; một tab báo lại `true`: về Online.
- [ ] Không beat quá hạn sống: sweeper đóng đoạn tại **lần beat cuối**, không phải lúc quét.
- [ ] Tab đang thao tác chết, tab còn lại đang vắng: chuyển Away tại lần beat cuối của tab chết.
- [ ] Tab A im lặng, tab B dùng thêm một lúc rồi để yên: Away tính từ lúc B để yên, không phải từ
      lúc A im lặng.
- [ ] Tab duy nhất im lặng, sau đó một tab khác mới xuất hiện: có khoảng offline ở giữa.
- [ ] Hai beat đồng thời khi đang offline: chỉ một đoạn được mở.
- [ ] F5 nhiều lần liên tiếp trên trình duyệt thật: vẫn online, một phiên.
- [ ] Kho sống không phản hồi: request thường không chậm đi; màn giám sát hiển thị theo lịch sử.
- [ ] Lỗi của một người không dừng sweeper của những người khác.
- [ ] `EXPLAIN` hai truy vấn của màn giám sát với dữ liệu cỡ thật: chỉ đi theo chỉ mục.
- [ ] Tab kết nối lại (socket mới mở trước khi socket cũ đóng): vẫn online, không sinh đoạn mới.
- [ ] Socket chưa đăng nhập hoặc hết phiên: bị từ chối, client không kết nối lại vô hạn.
- [ ] Socket không mở được: client tự chuyển sang kênh API, và quay lại socket khi mở được.
- [ ] WebSocket đi qua được **toàn bộ** đường proxy của bản triển khai thật (thử, đừng đoán).
- [ ] Xoá sạch kho sống: đoạn đang mở được đóng tại mốc an toàn.
- [ ] Đoạn vắt qua nửa đêm: thời lượng chia đúng cho hai ngày theo múi giờ nghiệp vụ.
- [ ] Người không có quyền không thấy dòng của người khác (cả ở danh sách lẫn chi tiết).
- [ ] Request bằng API token không tạo presence.

## 16. Trình tự triển khai

1. Chốt sáu quyết định: ai xem được, có Away hay không, định nghĩa offline, hạn lưu, có dùng để
   đánh giá hay không, kênh heartbeat (thử WebSocket qua đường proxy thật trước khi chọn).
2. Bảng `presence_segments` + hàm `transition` + kiểm thử.
3. Kho sống + hai hàm `beat` / `leave` + kiểm thử gộp nhiều tab (chưa cần kênh nào).
4. Sweeper + kiểm thử hết hạn và phục hồi.
5. Kênh: endpoint socket và/hoặc API gọi hai hàm trên; client gắn ở khung ứng dụng.
6. API đọc (trạng thái sống + thời lượng theo ngày) + phân quyền.
7. Màn giám sát, rồi timeline theo ngày.
8. Job xoá theo hạn lưu, tài liệu cho người dùng.
