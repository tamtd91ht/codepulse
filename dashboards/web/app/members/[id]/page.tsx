"use client";

import Link from "next/link";
import { use, useEffect, useState } from "react";
import { CartesianGrid, Legend, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

import { SubPanel, type SubTab, SubTabs } from "@/components/AdminTabs";
import { IconButton } from "@/components/icons";
import { scoreTone, useUrlRange } from "@/components/Evaluation";
import { MemberEvaluation } from "@/components/MemberEvaluation";
import { ReviewResults } from "@/components/ReviewResults";
import { Alert, Avatar, Card, ChartTip, EmptyState, Expander, Loading, PageHeader, Pill, SeverityBadge, StatStrip, StripStat } from "@/components/ui";
import { ApiError, api, qs, useApi } from "@/lib/api";
import { CATEGORY_LABEL, dateTime, weekLabel } from "@/lib/format";
import type {
  BoardFinding,
  CategoryWeek,
  Dispute,
  EvidenceList,
  Me,
  MetricsSeries,
  Page,
  Person,
  Repo,
  TelegramAccount,
  TelegramLinkCode,
} from "@/lib/types";

const TOP = 6;
/** GET /disputes returns at most this many (newest first). */
const DISPUTE_CAP = 200;

/** Where a dispute stands: waiting for the lead, or how it was decided. */
function DisputeState({ d }: { d: Dispute }) {
  if (d.decision === "accepted")
    return (
      <Pill tone="good" icon="check" title="Lead chấp nhận: finding bị loại, điểm review được tính lại">
        Chấp nhận
      </Pill>
    );
  if (d.decision === "rejected")
    return (
      <Pill tone="bad" icon="close" title="Lead từ chối: finding giữ nguyên">
        Từ chối
      </Pill>
    );
  return <Pill tone="warn">Chờ duyệt</Pill>;
}

/** Every dispute this person raised (yêu cầu người dùng 2026-11-03): what, why, and the outcome.
 * The API returns only those the viewer may see (docs/specs/09). */
function DisputeLog({ userId }: { userId: number }) {
  const { data, error, mutate } = useApi<Dispute[]>(`/disputes${qs({ pending: "false", user_id: userId })}`);
  const pending = data?.filter((d) => d.decision === null).length ?? 0;
  const accepted = data?.filter((d) => d.decision === "accepted").length ?? 0;
  const rejected = data?.filter((d) => d.decision === "rejected").length ?? 0;
  return (
    <Card
      id="member-disputes"
      title="Khiếu nại"
      subtitle={
        data && data.length
          ? `${data.length >= DISPUTE_CAP ? `${DISPUTE_CAP}+` : data.length} khiếu nại bạn được xem · ${accepted} chấp nhận · ${rejected} từ chối · ${pending} chờ duyệt`
          : undefined
      }
    >
      {!data ? (
        <Loading error={error} rows={3}>
          {error && (
            <button type="button" className="sm" onClick={() => void mutate()}>
              Thử lại
            </button>
          )}
        </Loading>
      ) : data.length === 0 ? (
        <EmptyState icon="scale" title="Không có khiếu nại nào bạn được xem">
          Khiếu nại (dispute) một finding được ghi lại ở đây, kèm lý do và kết quả duyệt; chỉ những khiếu nại trong phạm vi bạn được xem.
        </EmptyState>
      ) : (
        <>
          <ul className="finding-list">
            {data.slice(0, TOP).map((d) => (
              <DisputeItem key={d.id} d={d} />
            ))}
          </ul>
          {data.length > TOP && (
            <Expander summary={`${data.length - TOP} khiếu nại cũ hơn`}>
              <ul className="finding-list">
                {data.slice(TOP).map((d) => (
                  <DisputeItem key={d.id} d={d} />
                ))}
              </ul>
            </Expander>
          )}
        </>
      )}
    </Card>
  );
}

function DisputeItem({ d }: { d: Dispute }) {
  return (
    <li>
      {d.finding_severity ? <SeverityBadge severity={d.finding_severity} /> : null}
      <div className="body">
        {d.review_id ? <Link href={`/reviews/${d.review_id}`}>{d.finding_title ?? "Finding"}</Link> : (d.finding_title ?? "Finding đã xoá")}{" "}
        <DisputeState d={d} />
        {d.reason && <p className="small" style={{ margin: "4px 0", whiteSpace: "pre-wrap" }}>&ldquo;{d.reason}&rdquo;</p>}
        <div className="meta">
          {d.finding_file_path && (
            <>
              <code>
                {d.finding_file_path}
                {d.finding_line_start ? `:${d.finding_line_start}` : ""}
              </code>{" "}
              ·{" "}
            </>
          )}
          {d.finding_category && <>{CATEGORY_LABEL[d.finding_category] ?? d.finding_category} · </>}
          gửi {dateTime(d.created_at ?? null)}
          {d.decision && (
            <>
              {" "}
              · {d.decision === "accepted" ? "chấp nhận" : "từ chối"} bởi {d.decided_by_name ?? "—"} {dateTime(d.decided_at)}
            </>
          )}
        </div>
      </div>
    </li>
  );
}

function FindingItem({ f }: { f: BoardFinding }) {
  return (
    <li>
      <SeverityBadge severity={f.severity} />
      <div className="body">
        <Link href={`/reviews/${f.review_id}`}>{f.title}</Link>
        <div className="meta">
          <code>
            {f.file_path}:{f.line_start}
          </code>{" "}
          · {f.age_days} ngày
        </div>
      </div>
    </li>
  );
}

function Heatmap({ cells }: { cells: CategoryWeek[] }) {
  const weeks = [...new Set(cells.map((c) => c.week))].sort();
  const categories = [...new Set(cells.map((c) => c.category))].sort();
  if (!weeks.length)
    return (
      <EmptyState icon="checkCircle" tone="good" title="Chưa có finding nào trong 12 tuần">
        Loại lỗi theo tuần xuất hiện ở đây khi có review.
      </EmptyState>
    );
  const max = Math.max(...cells.map((c) => c.count));
  const count = (w: string, c: string) => cells.find((x) => x.week === w && x.category === c)?.count ?? 0;
  return (
    <div className="table-wrap" tabIndex={0} role="region" aria-label="Loại lỗi theo tuần (cuộn ngang)">
      <div
        className="heatmap"
        role="table"
        aria-label="Số finding theo loại lỗi và tuần"
        style={{ gridTemplateColumns: `120px repeat(${weeks.length}, minmax(34px, 1fr))` }}
      >
        <div role="row" style={{ display: "contents" }}>
          <span role="columnheader" aria-label="Loại lỗi" />
          {weeks.map((w) => (
            <span key={w} className="col-label" role="columnheader">
              {weekLabel(w)}
            </span>
          ))}
        </div>
        {categories.map((c) => (
          <div key={c} role="row" style={{ display: "contents" }}>
            <span className="row-label" role="rowheader">
              {CATEGORY_LABEL[c] ?? c}
            </span>
            {weeks.map((w) => {
              const n = count(w, c);
              // A narrow tint range keeps the default text colour readable in both themes.
              const alpha = n ? 0.15 + (0.35 * n) / max : 0;
              return (
                <span
                  key={w}
                  className="cell"
                  role="cell"
                  aria-label={`${n} finding`}
                  title={`${CATEGORY_LABEL[c] ?? c}, tuần ${weekLabel(w)}: ${n} finding`}
                  style={n ? { background: `color-mix(in srgb, var(--chart-1) ${Math.round(alpha * 100)}%, var(--surface-2))` } : undefined}
                >
                  {n || ""}
                </span>
              );
            })}
          </div>
        ))}
      </div>
    </div>
  );
}

/** Link this person's Telegram to CodePulse: a one-time code sent to the dự án's bot as /link CODE. */
function TelegramCard() {
  const [code, setCode] = useState<TelegramLinkCode | null>(null);
  // While a code is shown, poll so the list updates as soon as the person sends /link.
  const accounts = useApi<TelegramAccount[]>("/me/telegram", { refreshInterval: code ? 5000 : 0 });
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [unlinking, setUnlinking] = useState<number | null>(null);
  const [copied, setCopied] = useState<"ok" | "fail" | null>(null);

  async function copy(text: string) {
    try {
      if (!navigator.clipboard) throw new Error("no clipboard");
      await navigator.clipboard.writeText(text);
      setCopied("ok");
    } catch {
      setCopied("fail");
    }
    window.setTimeout(() => setCopied(null), 2500);
  }

  async function issue() {
    setBusy(true);
    setError(null);
    try {
      setCode(await api.post<TelegramLinkCode>("/me/telegram/link-code"));
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card title="Telegram" actions={<span className="muted small" title="Ví dụ /review mr 123, /review today">gửi lệnh review cho bot</span>}>
      {error && <Alert tone="error">Lỗi: {error}</Alert>}
      {accounts.error && <Alert tone="error">Không tải được liên kết: {accounts.error.message}</Alert>}
      {!accounts.data && !accounts.error && <Loading rows={1} />}
      {accounts.data && accounts.data.length > 0 && (
        <ul className="finding-list" style={{ marginBottom: 12 }}>
          {accounts.data.map((a) => (
            <li key={a.id}>
              <Pill tone="good" icon="check">
                Đã liên kết
              </Pill>
              <div className="body">
                {a.display_name ?? `Telegram ${a.external_key}`}
                <div className="meta">id {a.external_key}</div>
              </div>
              <IconButton
                icon="close"
                label="Gỡ liên kết"
                disabled={unlinking === a.id}
                onClick={async () => {
                  setUnlinking(a.id);
                  try {
                    await api.delete(`/me/telegram/${a.id}`);
                  } catch (e) {
                    // Already gone (double click, or unlinked from Telegram) is not an error here.
                    if (!(e instanceof ApiError && e.status === 404)) setError(e instanceof ApiError ? e.message : String(e));
                  } finally {
                    setUnlinking(null);
                    await accounts.mutate();
                  }
                }}
              />
            </li>
          ))}
        </ul>
      )}
      {code ? (
        <div className="link-code">
          <div className="code">{code.code}</div>
          <div className="small muted">
            Gửi cho bot: <code>/link {code.code}</code> · mã dùng một lần, hết hạn sau {Math.round(code.expires_in_s / 60)} phút.
          </div>
          <div style={{ display: "flex", gap: 8, marginTop: 10, alignItems: "center", flexWrap: "wrap" }}>
            <button className="sm" onClick={() => void copy(`/link ${code.code}`)}>
              Chép lệnh
            </button>
            <button
              className="sm ghost"
              onClick={() => {
                setCode(null);
                void accounts.mutate();
              }}
            >
              Xong
            </button>
            <span className="small muted" aria-live="polite">
              {copied === "ok" ? "Đã chép." : copied === "fail" ? "Không chép được, hãy chọn mã và sao chép thủ công." : ""}
            </span>
          </div>
        </div>
      ) : (
        <button className="primary sm" onClick={issue} disabled={busy}>
          {busy ? "Đang tạo…" : accounts.data?.length ? "Liên kết thêm thiết bị" : "Tạo mã liên kết"}
        </button>
      )}
    </Card>
  );
}

type MemberTab = "overview" | "evaluation" | "reviews" | "categories" | "disputes" | "telegram";
const MEMBER_TABS: MemberTab[] = ["overview", "evaluation", "reviews", "categories", "disputes", "telegram"];

/** The member page's sub-tab, kept in the URL hash (#evaluation) like Admin's, so a link or a
 * reload opens the same tab; Back / Forward follow it. */
function useHashTab(): [MemberTab, (t: MemberTab) => void] {
  const [tab, setTab] = useState<MemberTab>("overview");
  useEffect(() => {
    const read = () => {
      const h = window.location.hash.slice(1) as MemberTab;
      setTab(MEMBER_TABS.includes(h) ? h : "overview");
    };
    read();
    window.addEventListener("hashchange", read);
    return () => window.removeEventListener("hashchange", read);
  }, []);
  const go = (t: MemberTab) => {
    setTab(t);
    try {
      const url = new URL(window.location.href);
      url.hash = t === "overview" ? "" : t;
      window.history.pushState(null, "", url);
    } catch {
      /* the tab just is not kept in the URL */
    }
  };
  return [tab, go];
}

export default function MemberDetail({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const [tab, setTab] = useHashTab();
  const { data: person, error } = useApi<Person>(`/people/${id}`);
  const { data: me } = useApi<Me>("/auth/me");
  const { data: series, error: seriesError } = useApi<MetricsSeries>(`/metrics/users/${id}${qs({ period: "week" })}`);
  const { data: heat, error: heatError } = useApi<CategoryWeek[]>(
    tab === "categories" ? `/metrics/users/${id}/categories?weeks=12` : null,
  );
  const { data: repos } = useApi<Repo[]>("/repos");
  // One period for the evaluation and the review results: changing either card moves both. It
  // comes from the URL, so the Thành viên page's period follows a click on a name.
  const range = useUrlRange("30");
  // Evidence packs: the person, their lead and admins only (docs/specs/20 D4); a 403 hides the link.
  const { data: packs } = useApi<EvidenceList>(`/evidence/${id}`);
  const { data: open, error: openError } = useApi<Page<BoardFinding>>(`/findings${qs({ user_id: id, limit: 50 })}`);

  if (!person) return <Loading error={error} rows={4} />;
  // Stored weekly scores mix every dự án of the person: a dự án manager who is not their lead
  // does not get them (the evaluation below counts only their dự án).
  const seriesHidden = seriesError instanceof ApiError && seriesError.status === 403;
  const latest = series?.series.at(-1) ?? null;
  const rank = { critical: 0, high: 1, medium: 2, low: 3, info: 4 };
  const findings = [...(open?.items ?? [])].sort((a, b) => rank[a.severity] - rank[b.severity]);
  const name = person.full_name ?? person.primary_email ?? `#${person.id}`;
  const isMe = me?.id === person.id;
  const tabs: SubTab<MemberTab>[] = [
    { key: "overview", label: "Tổng quan", hint: "Chỉ số tuần gần nhất, xu hướng 12 tuần, finding đang mở" },
    { key: "evaluation", label: "Đánh giá", hint: "Đánh giá trong kỳ: kết luận, loại lỗi, theo tuần, theo repo" },
    { key: "reviews", label: "Kết quả review", hint: "Các review MR/commit trong kỳ" },
    { key: "categories", label: "Loại lỗi", hint: "Loại lỗi theo tuần (12 tuần)" },
    { key: "disputes", label: "Khiếu nại", hint: "Mọi khiếu nại đã gửi và kết quả duyệt" },
    ...(isMe ? [{ key: "telegram" as const, label: "Telegram", hint: "Gắn Telegram để gửi lệnh review cho bot" }] : []),
  ];
  // A #telegram link opened on someone else's page shows the overview instead.
  const shownTab: MemberTab = tabs.some((t) => t.key === tab) ? tab : "overview";

  return (
    <>
      <PageHeader
        eyebrow="Thành viên"
        title={name}
        subtitle={person.primary_email}
        avatar={<Avatar name={person.full_name} email={person.primary_email} size="lg" src={person.avatar_url} />}
        actions={
          packs && (
            <Link href={`/members/${person.id}/evidence`} className="button">
              Hồ sơ tháng (1:1)
            </Link>
          )
        }
      />

      <SubTabs<MemberTab>
        id="member"
        label="Nội dung trang thành viên"
        tabs={tabs}
        value={shownTab}
        onChange={setTab}
      />

      {shownTab === "overview" && (
        <SubPanel id="member" tab="overview">
          {!series && !seriesHidden && <Loading error={seriesError} rows={3} />}
          {series && (
            <StatStrip label="Tuần gần nhất">
              <StripStat
                label="Quality Index"
                title="Tuần gần nhất"
                value={latest?.quality_index}
                digits={1}
                change={series.delta.quality_index ?? null}
                tone={scoreTone(latest?.quality_index == null ? null : Number(latest.quality_index))}
              />
              <StripStat label="Điểm TB" title="Tuần gần nhất" value={latest?.avg_score} digits={1} change={series.delta.avg_score ?? null} />
              <StripStat
                label="Fix rate"
                title="Tỉ lệ lỗi critical/high đã sửa ở lần review sau"
                unit="%"
                value={latest?.fix_rate == null ? null : Number(latest.fix_rate) * 100}
              />
              <StripStat label="QI TB team" value={series.team_quality_index} digits={1} />
              <StripStat
                label="Finding mở"
                title="Chưa xử lý, mọi thời kỳ"
                value={open ? findings.length : undefined}
                unit={open?.next_cursor ? "+" : undefined}
                tone={findings.some((f) => f.severity === "critical") ? "bad" : findings.length ? "warn" : undefined}
              />
            </StatStrip>
          )}

          <div className="grid wide-left">
            <Card title="Xu hướng" actions={<span className="muted small">12 tuần</span>}>
              {series ? (
                <div className="chart-box">
                  <ResponsiveContainer>
                    <LineChart
                      data={series.series.map((m) => ({
                        week: weekLabel(m.period_start),
                        score: m.avg_score === null ? null : Number(m.avg_score),
                        qi: m.quality_index === null ? null : Number(m.quality_index),
                      }))}
                      margin={{ top: 8, right: 8, bottom: 0, left: -16 }}
                    >
                      <CartesianGrid vertical={false} stroke="var(--chart-grid)" />
                      <XAxis dataKey="week" tickLine={false} axisLine={false} fontSize={12} />
                      <YAxis domain={[0, 100]} tickLine={false} axisLine={false} fontSize={12} />
                      <Tooltip content={<ChartTip />} cursor={{ stroke: "var(--border-strong)" }} />
                      <Legend iconType="circle" iconSize={8} />
                      <Line dataKey="score" name="Điểm TB" stroke="var(--chart-1)" strokeWidth={2.5} dot={false} activeDot={{ r: 4 }} connectNulls />
                      <Line
                        dataKey="qi"
                        name="Quality Index"
                        stroke="var(--chart-2)"
                        strokeWidth={2.5}
                        strokeDasharray="6 4"
                        dot={false}
                        activeDot={{ r: 4 }}
                        connectNulls
                      />
                    </LineChart>
                  </ResponsiveContainer>
                </div>
              ) : seriesHidden ? (
                <p className="muted small">
                  Điểm theo tuần gộp mọi dự án của người này nên chỉ họ, lead của họ và admin xem được. Phần đánh giá và kết quả review trên
                  trang này chỉ tính việc trong dự án bạn quản lý.
                </p>
              ) : (
                <Loading error={seriesError} rows={4} />
              )}
            </Card>
            <Card title={open ? `Finding đang mở · ${findings.length}${open.next_cursor ? "+" : ""}` : "Finding đang mở"} actions={<span className="muted small">nặng nhất trước</span>}>
              {open ? (
                findings.length ? (
                  <>
                    <ul className="finding-list">
                      {findings.slice(0, TOP).map((f) => (
                        <FindingItem key={f.id} f={f} />
                      ))}
                    </ul>
                    {findings.length > TOP && (
                      <Expander summary={`${findings.length - TOP} finding khác`}>
                        <ul className="finding-list">
                          {findings.slice(TOP).map((f) => (
                            <FindingItem key={f.id} f={f} />
                          ))}
                        </ul>
                      </Expander>
                    )}
                  </>
                ) : (
                  <EmptyState icon="checkCircle" tone="good" title="Không có finding đang mở" />
                )
              ) : (
                <Loading error={openError} rows={4} />
              )}
            </Card>
          </div>
        </SubPanel>
      )}

      {shownTab === "evaluation" && (
        <SubPanel id="member" tab="evaluation">
          <MemberEvaluation person={person} range={range} />
        </SubPanel>
      )}

      {shownTab === "reviews" && (
        <SubPanel id="member" tab="reviews">
          <ReviewResults person={person.id} repos={repos} range={range} id="review-results-member" />
        </SubPanel>
      )}

      {shownTab === "categories" && (
        <SubPanel id="member" tab="categories">
          <Card id="member-heatmap" title="Loại lỗi theo tuần" actions={<span className="muted small">12 tuần</span>} collapsible={false}>
            {heat ? <Heatmap cells={heat} /> : <Loading error={heatError} rows={4} />}
          </Card>
        </SubPanel>
      )}

      {shownTab === "disputes" && (
        <SubPanel id="member" tab="disputes">
          <DisputeLog userId={person.id} />
        </SubPanel>
      )}

      {shownTab === "telegram" && isMe && (
        <SubPanel id="member" tab="telegram">
          <TelegramCard />
        </SubPanel>
      )}
    </>
  );
}
