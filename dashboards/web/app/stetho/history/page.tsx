"use client";

import { useCallback, useEffect, useState } from "react";

import { type SubTab, SubTabs } from "@/components/AdminTabs";
import { RangePicker, useUrlRange } from "@/components/Evaluation";
import { Icon } from "@/components/icons";
import { Alert, Card, Drawer, EmptyState, Loading, PageHeader, Pagination, Pill, StatStrip, StripStat } from "@/components/ui";
import { ApiError, api, qs, useApi } from "@/lib/api";
import { dateTimeMin, relative } from "@/lib/format";
import type { Me, Person } from "@/lib/types";

/*
 * Lịch sử Stetho (docs/specs/28 §9, user 2026-11-09): what people asked Stetho in the last 30
 * days — the question and a few points of each answer — with a report by member and by topic
 * tag. Admins only (ADMIN_EMAILS); not even holders of admin.* rights.
 */

type View = "members" | "tags" | "conversations";
interface TagDef {
  key: string;
  label: string;
}
interface Who {
  id: number;
  name: string;
  email: string | null;
}
interface Summary {
  keep_days: number;
  tags: TagDef[];
  kpis: { conversations: number; turns: number; people: number; untagged: number; cost_usd: number };
  by_member: { user: Who; conversations: number; turns: number; cost_usd: number; last_at: string; tags: { tag: string; n: number }[] }[];
  by_tag: { tag: string | null; label: string; n: number; people: number }[];
  by_day: { day: string; n: number }[];
}
interface ConversationRow {
  id: string;
  user: Who | null;
  title: string;
  turns: number;
  tag: string | null;
  tag_source: "ai" | "rule" | "manual" | null;
  started_at: string;
  last_at: string;
  cost_usd: number;
}
interface ConversationList {
  items: ConversationRow[];
  total: number;
  tags: TagDef[];
}
interface ConversationDetail extends Omit<ConversationRow, "turns"> {
  keep_until: string;
  tags: TagDef[];
  turns: {
    asked_at: string;
    question: string;
    answer_points: string[];
    answer_chars: number;
    status: "ok" | "error" | "cancelled";
    screen: string | null;
    model: string | null;
    cost_usd: number | null;
    duration_ms: number | null;
    docs: number;
    web: boolean;
  }[];
}

const PAGE_SIZE = 25;
const SOURCE_LABEL: Record<string, string> = { ai: "AI gắn", rule: "Gắn theo từ khoá", manual: "Admin gắn" };
const usd = (n: number) => `$${n.toFixed(n >= 10 ? 0 : 2)}`;

function useParam(name: string): [string, (v: string) => void] {
  const [value, setValue] = useState("");
  useEffect(() => {
    const read = () => setValue(new URLSearchParams(window.location.search).get(name) ?? "");
    read();
    window.addEventListener("popstate", read);
    return () => window.removeEventListener("popstate", read);
  }, [name]);
  const update = useCallback(
    (next: string) => {
      setValue(next);
      const url = new URL(window.location.href);
      if (next) url.searchParams.set(name, next);
      else url.searchParams.delete(name);
      window.history.replaceState(null, "", url);
    },
    [name],
  );
  return [value, update];
}

function TagSelect({ id, value, tags, onSaved }: { id: string; value: string | null; tags: TagDef[]; onSaved: () => void }) {
  const [busy, setBusy] = useState(false);
  return (
    <select
      className="compact"
      aria-label="Tag của cuộc trò chuyện"
      value={value ?? ""}
      disabled={busy}
      onClick={(e) => e.stopPropagation()}
      onChange={async (e) => {
        setBusy(true);
        try {
          await api.patch(`/stetho/history/conversations/${id}`, { tag: e.target.value });
          onSaved();
        } finally {
          setBusy(false);
        }
      }}
    >
      {!value && <option value="">Chưa gắn tag</option>}
      {tags.map((t) => (
        <option key={t.key} value={t.key}>
          {t.label}
        </option>
      ))}
    </select>
  );
}

function ConversationDrawer({ id, onClose, onChanged }: { id: string; onClose: () => void; onChanged: () => void }) {
  const { data, error, mutate } = useApi<ConversationDetail>(`/stetho/history/conversations/${id}`);
  const [removing, setRemoving] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  return (
    <Drawer
      width={640}
      title={data ? data.user?.name ?? "Cuộc trò chuyện" : "Cuộc trò chuyện"}
      onClose={onClose}
      actions={
        data ? (
          <TagSelect
            id={data.id}
            value={data.tag}
            tags={data.tags}
            onSaved={() => {
              void mutate();
              onChanged();
            }}
          />
        ) : undefined
      }
    >
      {!data ? (
        <Loading error={error} rows={6} />
      ) : (
        <div className="sh-detail">
          <p className="muted small">
            {dateTimeMin(data.started_at)} → {dateTimeMin(data.last_at)} · {data.turns.length} câu hỏi · {usd(data.cost_usd)} · tự xoá sau{" "}
            {dateTimeMin(data.keep_until)}
            {data.tag_source && <> · tag: {SOURCE_LABEL[data.tag_source]}</>}
          </p>
          <ol className="sh-turns">
            {data.turns.map((t, i) => (
              <li key={i} className="sh-turn">
                <div className="sh-turn-meta small muted">
                  {dateTimeMin(t.asked_at)}
                  {t.screen && <> · trên <code>{t.screen}</code></>}
                  {t.status !== "ok" && (
                    <Pill tone={t.status === "error" ? "bad" : "warn"}>{t.status === "error" ? "Lỗi" : "Đã dừng"}</Pill>
                  )}
                </div>
                <div className="sh-question">{t.question}</div>
                {t.answer_points.length > 0 ? (
                  <ul className="sh-points">
                    {t.answer_points.map((p, j) => (
                      <li key={j}>{p}</li>
                    ))}
                  </ul>
                ) : (
                  <p className="muted small">Không có nội dung trả lời.</p>
                )}
                <div className="small muted">
                  {[
                    t.model,
                    t.duration_ms ? `${(t.duration_ms / 1000).toFixed(1)} giây` : null,
                    t.cost_usd !== null ? usd(t.cost_usd) : null,
                    t.docs ? `${t.docs} đoạn tài liệu` : null,
                    t.web ? "có tra web" : null,
                    `trả lời ${t.answer_chars} ký tự (chỉ lưu ý chính)`,
                  ]
                    .filter(Boolean)
                    .join(" · ")}
                </div>
              </li>
            ))}
          </ol>
          {problem && <Alert tone="error">{problem}</Alert>}
          {removing ? (
            <div className="toolbar">
              <span>Xoá hẳn cuộc trò chuyện này?</span>
              <button
                type="button"
                className="sm danger"
                onClick={async () => {
                  try {
                    await api.delete(`/stetho/history/conversations/${data.id}`);
                    onChanged();
                    onClose();
                  } catch (e) {
                    setProblem(e instanceof ApiError ? e.message : String(e));
                  }
                }}
              >
                Xoá
              </button>
              <button type="button" className="sm ghost" onClick={() => setRemoving(false)}>
                Huỷ
              </button>
            </div>
          ) : (
            <button type="button" className="sm ghost" onClick={() => setRemoving(true)}>
              <Icon name="remove" size={14} /> Xoá cuộc trò chuyện
            </button>
          )}
        </div>
      )}
    </Drawer>
  );
}

export default function StethoHistoryPage() {
  const { data: me } = useApi<Me>("/auth/me");
  const admin = me?.is_admin === true;
  const people = useApi<Person[]>(admin ? "/people" : null);
  const [range, setRange] = useUrlRange("30");
  const [viewParam, setView] = useParam("view");
  const [user, setUser] = useParam("user");
  const [tag, setTag] = useParam("tag");
  const [q, setQ] = useParam("q");
  const [openId, setOpenId] = useParam("c");
  const [draft, setDraft] = useState("");
  const [page, setPage] = useState(1);
  const [notice, setNotice] = useState<string | null>(null);
  const view: View = viewParam === "tags" || viewParam === "conversations" ? viewParam : "members";
  useEffect(() => setDraft(q), [q]);
  useEffect(() => {
    const t = window.setTimeout(() => {
      if (draft.trim() !== q) setQ(draft.trim());
    }, 350);
    return () => window.clearTimeout(t);
  }, [draft, q, setQ]);
  useEffect(() => setPage(1), [user, tag, q, range?.from, range?.to, view]);

  const period = range ? { start: range.from, end: range.to } : null;
  const filters = { ...(period ?? {}), user_id: user || null, tag: tag || null };
  const summary = useApi<Summary>(admin && period ? `/stetho/history/summary${qs(filters)}` : null, { keepPreviousData: true });
  const list = useApi<ConversationList>(
    admin && period && view === "conversations"
      ? `/stetho/history/conversations${qs({ ...filters, q: q || null, page, page_size: PAGE_SIZE })}`
      : null,
    { keepPreviousData: true },
  );
  const s = summary.data;
  const tags = s?.tags ?? list.data?.tags ?? [];
  const refresh = () => {
    void summary.mutate();
    void list.mutate();
  };
  const openConversations = (patch: { user?: string; tag?: string }) => {
    if (patch.user !== undefined) setUser(patch.user);
    if (patch.tag !== undefined) setTag(patch.tag);
    setView("conversations");
  };

  if (me && !admin)
    return (
      <>
        <PageHeader title="Lịch sử Stetho" />
        <Card collapsible={false}>
          <EmptyState icon="alert" title="Chỉ admin xem được lịch sử Stetho" />
        </Card>
      </>
    );

  const views: SubTab<View>[] = [
    { key: "members", label: "Theo thành viên", count: s?.by_member.length },
    { key: "tags", label: "Theo chủ đề" },
    { key: "conversations", label: "Cuộc trò chuyện", count: s?.kpis.conversations },
  ];
  const maxTag = Math.max(1, ...(s?.by_tag.map((t) => t.n) ?? [1]));
  const active = [user, tag, q].filter(Boolean).length;

  return (
    <>
      <PageHeader
        eyebrow="Stetho"
        title="Lịch sử Stetho"
        subtitle="Câu hỏi của mọi người và vài ý chính của câu trả lời, 30 ngày gần nhất. Chỉ admin xem được; mỗi lần mở một cuộc trò chuyện được ghi nhật ký."
        actions={
          <button
            type="button"
            onClick={async () => {
              await api.post("/stetho/history/tag-now");
              setNotice("Đã xếp hàng gắn tag cho các cuộc trò chuyện đã dừng 15 phút; tải lại sau ít phút.");
            }}
          >
            <Icon name="sync" size={14} /> Gắn tag ngay
          </button>
        }
      />
      {notice && <Alert tone="success">{notice}</Alert>}
      <div className="report-bar">
        <div className="report-bar-top">
          <SubTabs<View> id="sh-view" label="Xem" variant="bar" tabs={views} value={view} onChange={(v) => setView(v === "members" ? "" : v)} />
          <div className="report-range">
            <RangePicker value={range} onChange={setRange} compact />
          </div>
        </div>
        <div className="report-filters open" role="group" aria-label="Bộ lọc lịch sử">
          <label className={`fsel${user ? " set" : ""}`}>
            <span>Thành viên</span>
            <select value={user} onChange={(e) => setUser(e.target.value)}>
              <option value="">Tất cả</option>
              {(people.data ?? []).map((p) => (
                <option key={p.id} value={p.id}>
                  {p.full_name ?? p.primary_email ?? `#${p.id}`}
                </option>
              ))}
            </select>
          </label>
          <label className={`fsel${tag ? " set" : ""}`}>
            <span>Chủ đề</span>
            <select value={tag} onChange={(e) => setTag(e.target.value)}>
              <option value="">Tất cả</option>
              {tags.map((t) => (
                <option key={t.key} value={t.key}>
                  {t.label}
                </option>
              ))}
              <option value="untagged">Chưa gắn tag</option>
            </select>
          </label>
          {view === "conversations" && (
            <label className="fsel jt-search">
              <Icon name="search" size={14} />
              <input type="search" value={draft} onChange={(e) => setDraft(e.target.value)} placeholder="Tìm trong câu hỏi" aria-label="Tìm" />
            </label>
          )}
          {active > 0 && (
            <button
              type="button"
              className="ghost sm report-clear"
              onClick={() => {
                setUser("");
                setTag("");
                setQ("");
                setDraft("");
              }}
            >
              <Icon name="close" size={14} /> Bỏ lọc
            </button>
          )}
        </div>
      </div>

      <StatStrip label="Tổng quan lịch sử">
        <StripStat label="Cuộc trò chuyện" value={s?.kpis.conversations} />
        <StripStat label="Câu hỏi" value={s?.kpis.turns} />
        <StripStat label="Người dùng" value={s?.kpis.people} />
        <StripStat label="Chưa gắn tag" value={s?.kpis.untagged} tone={s?.kpis.untagged ? "warn" : undefined} title="Gắn sau khi cuộc trò chuyện dừng 15 phút" />
        <StripStat label="Chi phí" value={s ? Number(s.kpis.cost_usd.toFixed(2)) : undefined} digits={2} unit="$" />
      </StatStrip>

      {view === "members" && (
        <Card collapsible={false} flush>
          {!s ? (
            <Loading error={summary.error} rows={5} />
          ) : s.by_member.length === 0 ? (
            <EmptyState icon="inbox" title="Chưa ai hỏi Stetho trong khoảng này" />
          ) : (
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Thành viên</th>
                    <th className="num">Cuộc trò chuyện</th>
                    <th className="num">Câu hỏi</th>
                    <th>Chủ đề chính</th>
                    <th>Gần nhất</th>
                    <th className="num">Chi phí</th>
                  </tr>
                </thead>
                <tbody>
                  {s.by_member.map((m) => (
                    <tr key={m.user.id} className="jt-row" onClick={() => openConversations({ user: String(m.user.id) })}>
                      <td>
                        <b>{m.user.name}</b>
                        {m.user.email && <div className="muted small">{m.user.email}</div>}
                      </td>
                      <td className="num">{m.conversations}</td>
                      <td className="num">{m.turns}</td>
                      <td>
                        <span className="sh-tags">
                          {m.tags.length ? (
                            m.tags.map((t) => (
                              <Pill key={t.tag} tone="info">
                                {tags.find((d) => d.key === t.tag)?.label ?? t.tag} · {t.n}
                              </Pill>
                            ))
                          ) : (
                            <span className="muted small">chưa gắn tag</span>
                          )}
                        </span>
                      </td>
                      <td className="small muted">{relative(m.last_at)}</td>
                      <td className="num">{usd(m.cost_usd)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Card>
      )}

      {view === "tags" && (
        <Card collapsible={false} title="Chủ đề được hỏi" subtitle="Bấm một chủ đề để xem các cuộc trò chuyện của nó.">
          {!s ? (
            <Loading error={summary.error} rows={5} />
          ) : s.by_tag.length === 0 ? (
            <EmptyState icon="inbox" title="Chưa có cuộc trò chuyện nào" />
          ) : (
            <ul className="sh-bars">
              {s.by_tag.map((t) => (
                <li key={t.tag ?? "none"}>
                  <button type="button" className="sh-bar" onClick={() => openConversations({ tag: t.tag ?? "untagged" })}>
                    <span className="sh-bar-label">{t.label}</span>
                    <span className="sh-bar-track" aria-hidden="true">
                      <span className="sh-bar-fill" style={{ width: `${Math.round((t.n / maxTag) * 100)}%` }} />
                    </span>
                    <span className="sh-bar-n">
                      <b>{t.n}</b>
                      <span className="muted small">
                        {" "}
                        · {Math.round((t.n / Math.max(1, s.kpis.conversations)) * 100)}% · {t.people} người
                      </span>
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </Card>
      )}

      {view === "conversations" && (
        <Card collapsible={false} flush>
          {!list.data ? (
            <Loading error={list.error} rows={6} />
          ) : list.data.items.length === 0 ? (
            <EmptyState icon="inbox" title="Không có cuộc trò chuyện nào khớp" />
          ) : (
            <div className={list.isLoading ? "eval-dim" : undefined}>
              <div className="table-wrap">
                <table>
                  <thead>
                    <tr>
                      <th>Thời gian</th>
                      <th>Thành viên</th>
                      <th>Câu hỏi đầu</th>
                      <th className="num">Câu</th>
                      <th>Chủ đề</th>
                      <th className="num">Chi phí</th>
                    </tr>
                  </thead>
                  <tbody>
                    {list.data.items.map((c) => (
                      <tr key={c.id} className="jt-row" onClick={() => setOpenId(c.id)}>
                        <td className="small" title={dateTimeMin(c.last_at)}>
                          {relative(c.last_at)}
                        </td>
                        <td>{c.user?.name ?? "—"}</td>
                        <td className="sh-title">{c.title}</td>
                        <td className="num">{c.turns}</td>
                        <td onClick={(e) => e.stopPropagation()}>
                          <TagSelect id={c.id} value={c.tag} tags={list.data?.tags ?? tags} onSaved={refresh} />
                          {c.tag_source && <div className="muted small">{SOURCE_LABEL[c.tag_source]}</div>}
                        </td>
                        <td className="num">{usd(c.cost_usd)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <Pagination page={page} pageSize={PAGE_SIZE} total={list.data.total} shown={list.data.items.length} onPage={setPage} label="cuộc trò chuyện" />
            </div>
          )}
        </Card>
      )}

      {openId && <ConversationDrawer key={openId} id={openId} onClose={() => setOpenId("")} onChanged={refresh} />}
    </>
  );
}

