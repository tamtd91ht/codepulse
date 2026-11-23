"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { DateRangePicker, type DayRange } from "@/components/DateRangePicker";
import { Icon, IconButton } from "@/components/icons";
import { MultiSelect } from "@/components/MultiSelect";
import { Alert, Avatar, Card, Drawer, EmptyState, Expander, Loading, SortTh, StatStrip, StripStat, useSort } from "@/components/ui";
import { api, qs, useApi } from "@/lib/api";
import { dayLabel, localDay, shiftDays } from "@/lib/format";
import {
  clock,
  duration,
  framesOf,
  PRESENCE_READY,
  type PresenceBoard,
  type PresenceDay,
  type PresenceDevice,
  type PresenceRow,
  type PresenceState,
  type PresenceUserDays,
  STATE_LABEL,
} from "@/lib/presence";

/*
 * Trạng thái online (docs/specs/33). The board: everyone one may see, their state now and their
 * totals for one day, refreshed every POLL_MS. One person's days (a 24-hour bar per day) open in a
 * drawer; someone who sees only themselves gets their days on the page.
 */

const POLL_MS = 10_000;
const STATE_ORDER: Record<PresenceState, number> = { online: 0, away: 1, offline: 2 };
const HOURS = [0, 6, 12, 18, 24];

function StateDot({ state }: { state: PresenceState }) {
  return <span className={`prs-dot ${state}`} aria-hidden="true" />;
}

function secondsSince(since: string | null, now: string): number | null {
  if (!since) return null;
  return Math.max(0, Math.round((new Date(now).getTime() - new Date(since).getTime()) / 1000));
}

function StateCell({ row, now, tz }: { row: PresenceRow; now: string; tz: string }) {
  const elapsed = secondsSince(row.since, now);
  const sub =
    row.state === "offline"
      ? row.since
        ? `từ ${elapsed !== null && elapsed < 86_400 ? clock(row.since, tz) : dayLabel(row.since.slice(0, 10))} · ${elapsed !== null && elapsed < 60 ? "vừa xong" : duration(elapsed)}`
        : "chưa từng online"
      : elapsed !== null && elapsed >= 60
        ? `${duration(elapsed)}`
        : "vừa xong";
  return (
    <div className="prs-state">
      <StateDot state={row.state} />
      <div>
        <div className="prs-state-name">{STATE_LABEL[row.state]}</div>
        <div className="muted small">{sub}</div>
      </div>
    </div>
  );
}

function devicesText(row: PresenceRow): string {
  // Which devices: the cell opens the list (`OpenDevices`), so the text stays short.
  return row.tabs ? `${row.devices} thiết bị · ${row.tabs} tab` : "–";
}

function deviceLine(d: PresenceDevice, tz: string): string {
  return `${d.label} · ${d.tabs} tab · ${d.active ? "đang thao tác" : "để yên"} · mở từ ${clock(d.since, tz)}`;
}

/**
 * The "Đang mở" cell: the counts, and the devices behind them. Hovering shows them as a tooltip;
 * a click (or Enter) opens the list, placed with `position: fixed` so the table's scroll box does
 * not cut it. Escape, a click elsewhere or a scroll closes it.
 */
function OpenDevices({ row, tz }: { row: PresenceRow; tz: string }) {
  const [at, setAt] = useState<{ top: number; right: number } | null>(null);
  const button = useRef<HTMLButtonElement>(null);
  const pop = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!at) return;
    const close = () => setAt(null);
    const away = (e: PointerEvent) => {
      const target = e.target as Node;
      if (!pop.current?.contains(target) && !button.current?.contains(target)) close();
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Escape") return;
      close();
      button.current?.focus();
    };
    document.addEventListener("pointerdown", away);
    window.addEventListener("keydown", onKey);
    window.addEventListener("scroll", close, true);
    window.addEventListener("resize", close);
    return () => {
      document.removeEventListener("pointerdown", away);
      window.removeEventListener("keydown", onKey);
      window.removeEventListener("scroll", close, true);
      window.removeEventListener("resize", close);
    };
  }, [at]);
  if (!row.tabs) return <>–</>;
  const toggle = () => {
    if (at) return setAt(null);
    const box = button.current?.getBoundingClientRect();
    if (box) setAt({ top: box.bottom + 6, right: Math.max(8, window.innerWidth - box.right) });
  };
  return (
    <>
      <button
        type="button"
        ref={button}
        className="prs-open"
        aria-expanded={!!at}
        aria-haspopup="dialog"
        title={row.open.map((d) => deviceLine(d, tz)).join("\n")}
        onClick={toggle}
      >
        {devicesText(row)}
      </button>
      {at && (
        <div ref={pop} className="prs-pop" role="dialog" aria-label={`Thiết bị đang mở của ${row.user.full_name ?? row.user.email ?? "người này"}`} style={{ top: at.top, right: at.right }}>
          <div className="prs-pop-head">
            {row.devices} thiết bị · {row.tabs} tab
          </div>
          <ul>
            {row.open.map((d, i) => (
              <li key={`${d.label}-${d.since}-${i}`}>
                <StateDot state={d.active ? "online" : "away"} />
                <div>
                  <div className="prs-pop-name">{d.label}</div>
                  <div className="muted small">
                    {d.tabs} tab · {d.active ? "đang thao tác" : "để yên"} · mở từ {clock(d.since, tz)}
                  </div>
                </div>
              </li>
            ))}
          </ul>
          <p className="muted small prs-pop-note">Tên là trình duyệt và hệ điều hành; trình duyệt không cho biết tên máy.</p>
        </div>
      )}
    </>
  );
}

export function PresenceScreen() {
  const today = localDay();
  const [day, setDay] = useState("");
  const [query, setQuery] = useState("");
  const [teams, setTeams] = useState<number[]>([]);
  const [state, setState] = useState<"" | PresenceState>("");
  const [open, setOpen] = useState<PresenceRow | null>(null);
  // docs/specs/33 §14: the person to sign out, or "all"; asked before it is done.
  const [kick, setKick] = useState<PresenceRow | "all" | null>(null);
  const [kicked, setKicked] = useState<string | null>(null);
  const board = useApi<PresenceBoard>(`/presence${qs({ day: day || null })}`, { keepPreviousData: true, refreshInterval: POLL_MS });
  const data = board.data;
  // This tab's own socket may register after the first read: read again once it has.
  const reload = board.mutate;
  useEffect(() => {
    const again = () => void reload();
    window.addEventListener(PRESENCE_READY, again);
    return () => window.removeEventListener(PRESENCE_READY, again);
  }, [reload]);

  const shown = useMemo(() => {
    const needle = query.trim().toLowerCase();
    return (data?.rows ?? []).filter(
      (row) =>
        (!state || row.state === state) &&
        (!teams.length || row.teams.some((t) => teams.includes(t.id))) &&
        (!needle || `${row.user.full_name ?? ""} ${row.user.email ?? ""}`.toLowerCase().includes(needle)),
    );
  }, [data, query, teams, state]);
  const sortValue = useCallback((row: PresenceRow, key: string): number | string | null => {
    switch (key) {
      case "name":
        return row.user.full_name ?? row.user.email ?? "";
      case "state":
        return STATE_ORDER[row.state];
      case "online":
        return row.day.online_s;
      case "away":
        return row.day.away_s;
      case "offline":
        return row.day.offline_s;
      case "first":
        return row.day.first_seen;
      case "last":
        return row.day.last_seen;
      default:
        return null;
    }
  }, []);
  const { sorted, sort, toggle } = useSort(shown, { key: "state", dir: "asc" }, sortValue);

  if (board.error && !data) return <Alert tone="error">Không tải được trạng thái online: {board.error.message}</Alert>;
  if (!data) return <Loading rows={8} />;

  const own = data.scope === "own";
  const dayName = data.day === data.today ? "hôm nay" : dayLabel(data.day);
  const filtered = Boolean(query || teams.length || state);

  return (
    <>
      <p className="prs-note muted small">
        CodePulse ghi nhận thời gian bạn mở ứng dụng: <b>Online</b> khi có thao tác trong {data.idle_minutes} phút gần nhất,{" "}
        <b>Tạm vắng</b> khi tab còn mở nhưng không thao tác, <b>Offline</b> khi không còn tab nào. Chỉ thời gian, không ghi trang đang
        xem hay nội dung. Số liệu giữ {data.keep_days} ngày, chỉ để giám sát, <b>không tính vào DPI hay điểm đánh giá</b>.
        {own ? " Bạn chỉ thấy số liệu của chính mình; lead và quản lý thấy của team." : ""}
      </p>
      {kicked && <Alert tone="success">{kicked}</Alert>}
      {!data.live && <Alert tone="warning">Không đọc được trạng thái trực tiếp (Redis không phản hồi): đang hiển thị theo lịch sử đã ghi.</Alert>}
      {board.error && <Alert tone="error">Không làm mới được: {board.error.message}. Đang hiển thị số liệu lần trước.</Alert>}

      {!own && (
        <StatStrip label="Số người theo trạng thái hiện tại">
          <StripStat label="Online" value={data.counts.online} tone={data.counts.online ? "good" : undefined} sub="đang thao tác" />
          <StripStat label="Tạm vắng" value={data.counts.away} tone={data.counts.away ? "warn" : undefined} sub={`không thao tác ≥ ${data.idle_minutes} phút`} />
          <StripStat label="Offline" value={data.counts.offline} sub="không mở ứng dụng" />
          <StripStat label="Tổng" value={data.rows.length} sub={data.scope === "all" ? "mọi người đang hoạt động" : "team của bạn"} />
        </StatStrip>
      )}

      <Card flush collapsible={false}>
        <div className="toolbar filters prs-filters">
          {!own && (
            <label className="filter search">
              <span className="muted small">Tìm</span>
              <input type="search" value={query} placeholder="Tên hoặc email" onChange={(e) => setQuery(e.target.value)} />
            </label>
          )}
          {!own && data.teams.length > 1 && (
            <div className="filter">
              <span className="muted small">Team</span>
              <MultiSelect<number>
                label="Team"
                options={data.teams.map((t) => ({ value: t.id, label: t.name }))}
                value={teams}
                onChange={setTeams}
                empty="Mọi team"
                unit="team"
              />
            </div>
          )}
          {!own && (
            <label className="filter">
              <span className="muted small">Trạng thái</span>
              <select value={state} onChange={(e) => setState(e.target.value as "" | PresenceState)}>
                <option value="">Tất cả</option>
                <option value="online">Online</option>
                <option value="away">Tạm vắng</option>
                <option value="offline">Offline</option>
              </select>
            </label>
          )}
          <label className="filter">
            <span className="muted small">Thời lượng của ngày</span>
            <input type="date" value={day || data.today} min={shiftDays(today, -data.keep_days)} max={data.today} onChange={(e) => setDay(e.target.value === data.today ? "" : e.target.value)} />
          </label>
          {data.can_sign_out && (
            <button type="button" className="ghost danger prs-kick-all" onClick={() => setKick("all")}>
              <Icon name="logout" size={14} /> Đăng xuất tất cả
            </button>
          )}
          <span className="muted small prs-count">
            {filtered ? `${sorted.length} / ${data.rows.length} người` : `${data.rows.length} người`} · cập nhật {clock(data.now, data.tz)}
          </span>
        </div>
        {sorted.length === 0 ? (
          <EmptyState icon="members" title="Không có ai khớp bộ lọc">
            Bỏ bớt điều kiện lọc để xem lại danh sách.
          </EmptyState>
        ) : (
          <div className="table-wrap">
            <table className="prs-table">
              <thead>
                <tr>
                  <SortTh id="name" sort={sort} onSort={toggle}>
                    Thành viên
                  </SortTh>
                  <SortTh id="state" sort={sort} onSort={toggle}>
                    Hiện tại
                  </SortTh>
                  <SortTh id="online" sort={sort} onSort={toggle} num>
                    Online {dayName}
                  </SortTh>
                  <SortTh id="away" sort={sort} onSort={toggle} num>
                    Tạm vắng
                  </SortTh>
                  <SortTh id="offline" sort={sort} onSort={toggle} num>
                    Offline
                  </SortTh>
                  <SortTh id="first" sort={sort} onSort={toggle}>
                    Bắt đầu
                  </SortTh>
                  <SortTh id="last" sort={sort} onSort={toggle}>
                    Lần cuối
                  </SortTh>
                  <th>Đang mở</th>
                  <th className="prs-actions" aria-label="Thao tác" />
                </tr>
              </thead>
              <tbody>
                {sorted.map((row) => (
                  <tr key={row.user.id}>
                    <td>
                      <div className="cell-main">
                        <Avatar name={row.user.full_name} email={row.user.email} size="sm" src={row.user.avatar_url} />
                        <div className="who">
                          <div>{row.user.full_name ?? row.user.email ?? `#${row.user.id}`}</div>
                          {row.teams.length > 0 && <div className="muted small">{row.teams.map((t) => t.name).join(", ")}</div>}
                        </div>
                      </div>
                    </td>
                    <td>
                      <StateCell row={row} now={data.now} tz={data.tz} />
                    </td>
                    <td className="num">{duration(row.day.online_s)}</td>
                    <td className="num">{duration(row.day.away_s)}</td>
                    <td className="num" title="Tổng các khoảng không mở ứng dụng giữa lần đầu và lần cuối online trong ngày">
                      {duration(row.day.offline_s)}
                    </td>
                    <td>{clock(row.day.first_seen, data.tz)}</td>
                    <td>{row.day.last_seen && row.state !== "offline" && data.day === data.today ? "đang mở" : clock(row.day.last_seen, data.tz)}</td>
                    <td className="muted small">
                      <OpenDevices row={row} tz={data.tz} />
                    </td>
                    <td className="prs-actions">
                      <IconButton icon="history" label="Xem lịch sử theo ngày" className="tip-left" onClick={() => setOpen(row)} />
                      {data.can_sign_out && (
                        <IconButton icon="logout" label="Đăng xuất tài khoản này" tone="danger" className="tip-left" onClick={() => setKick(row)} />
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      {own && data.rows[0] && (
        <Card title="Lịch sử của tôi" collapsible={false}>
          <PersonDays userId={data.rows[0].user.id} today={data.today} keepDays={data.keep_days} />
        </Card>
      )}
      {kick && (
        <ConfirmSignOut
          target={kick}
          others={data.rows.length}
          onCancel={() => setKick(null)}
          onDone={(message) => {
            setKick(null);
            setKicked(message);
            void reload();
          }}
        />
      )}
      {open && (
        <Drawer title={`Lịch sử online · ${open.user.full_name ?? open.user.email ?? `#${open.user.id}`}`} width={760} onClose={() => setOpen(null)}>
          <PersonDays userId={open.user.id} today={data.today} keepDays={data.keep_days} />
        </Drawer>
      )}
    </>
  );
}

/** docs/specs/33 §14: ask, then end every session of one person, or of everyone else. */
function ConfirmSignOut({
  target,
  others,
  onCancel,
  onDone,
}: {
  target: PresenceRow | "all";
  others: number;
  onCancel: () => void;
  onDone: (message: string) => void;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const all = target === "all";
  const name = all ? "" : (target.user.full_name ?? target.user.email ?? `#${target.user.id}`);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && !busy && onCancel();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [busy, onCancel]);
  const run = async () => {
    setBusy(true);
    setError(null);
    try {
      if (all) {
        const done = await api.post<{ accounts: number }>("/presence/sign-out-all");
        onDone(`Đã đăng xuất ${done.accounts} tài khoản. Họ phải đăng nhập lại qua GitLab.`);
      } else {
        await api.post(`/presence/users/${target.user.id}/sign-out`);
        onDone(`Đã đăng xuất ${name}. Tài khoản này phải đăng nhập lại qua GitLab.`);
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : "Không thực hiện được");
      setBusy(false);
    }
  };
  return (
    <div className="modal-backdrop" onClick={() => !busy && onCancel()}>
      <div className="modal card prs-confirm" role="dialog" aria-modal="true" aria-labelledby="prs-confirm-title" onClick={(e) => e.stopPropagation()}>
        <div className="modal-head">
          <h2 id="prs-confirm-title">{all ? "Đăng xuất tất cả tài khoản?" : `Đăng xuất ${name}?`}</h2>
          <IconButton icon="close" label="Đóng" onClick={onCancel} disabled={busy} />
        </div>
        <p>
          {all ? (
            <>
              Mọi phiên đăng nhập của <b>tất cả tài khoản khác</b> ({Math.max(others - 1, 0)} người trong danh sách, và cả những người
              không hiển thị) kết thúc ngay, trên mọi tab và thiết bị. <b>Riêng bạn vẫn giữ phiên đang dùng.</b>
            </>
          ) : (
            <>
              Mọi phiên đăng nhập của <b>{name}</b> kết thúc ngay, trên mọi tab và thiết bị.
            </>
          )}
        </p>
        <p className="muted small">
          Người bị đăng xuất phải đăng nhập lại qua GitLab; tài khoản <b>không bị khoá</b>. Tab đang mở tự chuyển về trang đăng nhập trong
          khoảng 30 giây; việc đang nhập dở chưa lưu sẽ mất. Token MCP / API cá nhân không bị ảnh hưởng. Thao tác được ghi vào nhật ký.
        </p>
        {error && <Alert tone="error">{error}</Alert>}
        <div className="toolbar prs-confirm-actions">
          <button type="button" className="danger" onClick={run} disabled={busy} autoFocus>
            {busy ? "Đang đăng xuất…" : all ? "Đăng xuất tất cả" : "Đăng xuất"}
          </button>
          <button type="button" onClick={onCancel} disabled={busy}>
            Huỷ
          </button>
        </div>
      </div>
    </div>
  );
}

/** One person's days: totals of the range, then a 24-hour bar per day. */
function PersonDays({ userId, today, keepDays }: { userId: number; today: string; keepDays: number }) {
  const [range, setRange] = useState<DayRange>({ from: shiftDays(today, -6), to: today });
  const quickPicks = useMemo(
    () => [7, 14, 30].map((n) => ({ label: `${n} ngày`, range: { from: shiftDays(today, -(n - 1)), to: today } })),
    [today],
  );
  const days = useApi<PresenceUserDays>(`/presence/users/${userId}${qs({ from: range.from, to: range.to })}`, {
    keepPreviousData: true,
    refreshInterval: range.to === today ? 30_000 : 0,
  });
  const data = days.data;
  return (
    <div className="prs-days">
      <div className="prs-days-head">
        <DateRangePicker label="Khoảng ngày" value={range} min={shiftDays(today, -keepDays)} max={today} maxSpan={62} onChange={setRange} quickPicks={quickPicks} />
        <div className="prs-legend muted small" aria-hidden="true">
          <span>
            <i className="prs-key online" /> Online
          </span>
          <span>
            <i className="prs-key away" /> Tạm vắng
          </span>
          <span>
            <i className="prs-key offline" /> Offline
          </span>
        </div>
      </div>
      {days.error && !data ? (
        <Alert tone="error">Không tải được lịch sử: {days.error.message}</Alert>
      ) : !data ? (
        <Loading rows={5} />
      ) : (
        <>
          <StatStrip label="Tổng trong khoảng ngày" inline>
            <TimeStat label="Online" seconds={data.totals.online_s} />
            <TimeStat label="Tạm vắng" seconds={data.totals.away_s} />
            <TimeStat label="Offline giữa giờ" seconds={data.totals.offline_s} title="Tổng các khoảng không mở ứng dụng giữa lần đầu và lần cuối online của từng ngày" />
            <StripStat label="Ngày có online" value={data.days.filter((d) => d.sessions > 0).length} sub={`trên ${data.days.length} ngày`} />
          </StatStrip>
          <div className="prs-axis muted small" aria-hidden="true">
            <span />
            <div className="prs-hours">
              {HOURS.map((h) => (
                <span key={h} style={{ left: `${(h / 24) * 100}%` }}>
                  {h}h
                </span>
              ))}
            </div>
          </div>
          <ul className="prs-daylist">
            {data.days.map((d, i) => (
              <DayRow key={d.day} day={d} tz={data.tz} open={i === 0} live={d.day === today ? data.now : null} />
            ))}
          </ul>
        </>
      )}
    </div>
  );
}

/** A duration in the stat strip (StripStat formats numbers, not times). */
function TimeStat({ label, seconds, title }: { label: string; seconds: number; title?: string }) {
  return (
    <div className="kstat" title={title}>
      <span className="kstat-label">{label}</span>
      <span className="kstat-value prs-time">{duration(seconds)}</span>
    </div>
  );
}

/**
 * One day: the 24-hour bar, the totals, and the frames spelled out ("08:24 – 10:30 Online",
 * the offline ones between them too). `live`: the server's now when the day is today, so the
 * frame still going on says so. The newest day opens its frames; the others on a click.
 */
function DayRow({ day, tz, open, live }: { day: PresenceDay; tz: string; open: boolean; live: string | null }) {
  const frames = framesOf(day.stretches);
  const last = frames[frames.length - 1];
  // The stretch that runs to the server's "now" is still going on.
  const ongoing = live && last && Math.abs(new Date(last.end).getTime() - new Date(live).getTime()) < 2_000 ? last : null;
  const start = new Date(day.start).getTime();
  const span = new Date(day.end).getTime() - start;
  const summary = day.sessions
    ? `${clock(day.first_seen, tz)} → ${clock(day.last_seen, tz)} · online ${duration(day.online_s)}` +
      (day.away_s ? ` · vắng ${duration(day.away_s)}` : "") +
      (day.offline_s ? ` · offline ${duration(day.offline_s)}` : "") +
      (day.sessions > 1 ? ` · ${day.sessions} phiên` : "")
    : "Không online";
  return (
    <li className="prs-day">
      <span className="prs-day-name">{dayLabel(day.day)}</span>
      <div>
        <div className="prs-bar" role="img" aria-label={`${dayLabel(day.day)}: ${summary}`}>
          {day.stretches.map((s) => {
            const from = new Date(s.start).getTime();
            const to = new Date(s.end).getTime();
            return (
              <span
                key={s.start}
                className={`prs-seg ${s.state}`}
                style={{ left: `${((from - start) / span) * 100}%`, width: `${Math.max(((to - from) / span) * 100, 0.15)}%` }}
                title={`${clock(s.start, tz)} – ${clock(s.end, tz)} · ${STATE_LABEL[s.state]} ${duration((to - from) / 1000)}`}
              />
            );
          })}
        </div>
        <div className={`small ${day.sessions ? "prs-day-sum" : "muted"}`}>{summary}</div>
        {frames.length > 0 && (
          <Expander summary={`Khung giờ (${frames.length})`} open={open}>
            <ol className="prs-frames">
              {frames.map((f) => (
                <li key={f.start} className={f.state}>
                  <StateDot state={f.state} />
                  <span className="prs-frame-time">
                    {clock(f.start, tz)} – {f === ongoing ? "hiện tại" : clock(f.end, tz)}
                  </span>
                  <span className="prs-frame-state">{STATE_LABEL[f.state]}</span>
                  <span className="muted">{duration(f.seconds)}</span>
                </li>
              ))}
            </ol>
          </Expander>
        )}
      </div>
    </li>
  );
}
