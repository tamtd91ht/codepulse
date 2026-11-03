"use client";

/**
 * Evidence pack (docs/specs/20 §6): one person's month for the 1:1. Coaching only (D1): no score;
 * seen by the person, their lead and admins (D4). States and rights: §6.2b, decided by the API.
 */

import Link from "next/link";
import { type FormEvent, type ReactNode, use, useEffect, useRef, useState } from "react";
import { useSWRConfig } from "swr";

import { Alert, Card, EmptyState, ExternalLink, Loading, Message, PageHeader, Pager, Pill, SeverityBadge, StatStrip, StripStat } from "@/components/ui";
import { ApiError, api, useApi } from "@/lib/api";
import { CATEGORY_LABEL, dateTime, num } from "@/lib/format";
import type { EvidenceKind, EvidenceMr, EvidencePack, EvidenceStatus } from "@/lib/types";

const STEPS: { status: EvidenceStatus; label: string }[] = [
  { status: "draft", label: "Nháp" },
  { status: "submitted", label: "Đã gửi lead" },
  { status: "reviewed", label: "Lead đã xem" },
  { status: "shared", label: "Đã chốt sau 1:1" },
];
const KIND_LABEL: Record<EvidenceKind, string> = {
  mr: "MR",
  commit: "Commit",
  review_given: "Review cho người khác",
  finding_fixed: "Sửa lỗi",
  incident: "Xử lý sự cố",
  design: "Thiết kế / nghiên cứu",
  mentoring: "Hướng dẫn đồng đội",
  other: "Khác",
};
const MAX_BACK = 24;

const thisMonth = () => {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}`;
};
const shift = (month: string, by: number) => {
  const [y, m] = month.split("-").map(Number);
  const i = y * 12 + (m - 1) + by;
  return `${Math.floor(i / 12)}-${String((i % 12) + 1).padStart(2, "0")}`;
};
const monthsBetween = (a: string, b: string) => {
  const [ya, ma] = a.split("-").map(Number);
  const [yb, mb] = b.split("-").map(Number);
  return (yb - ya) * 12 + (mb - ma);
};
const monthLabel = (month: string) => `Tháng ${Number(month.slice(5, 7))}/${month.slice(0, 4)}`;
const hours = (h: number | null) => (h === null ? "—" : h < 48 ? `${num(h, 1)} giờ` : `${num(h / 24, 1)} ngày`);

function MrLine({ m, why }: { m: EvidenceMr; why?: string }) {
  const text = `!${m.iid} ${m.title ?? ""}`;
  return (
    <li>
      <div className="body">
        {m.web_url ? <ExternalLink href={m.web_url}>{text}</ExternalLink> : text} {why && <Pill tone="info">{why}</Pill>}
        <div className="meta">
          {m.path_with_namespace} · {m.size_bucket ?? "—"} · mở → merge {hours(m.time_to_merge)}
          {m.review_rounds ? ` · ${m.review_rounds} vòng review` : ""}
          {!m.reviewed && " · merge không review"}
        </div>
      </div>
    </li>
  );
}

/** A text the viewer may edit now (or only read), saved on its own. */
function NoteBox({
  label,
  hint,
  value,
  editable,
  onSave,
  onDirty,
}: {
  label: string;
  hint: string;
  value: string | null;
  editable: boolean;
  onSave: (text: string) => Promise<void>;
  onDirty: (dirty: boolean) => void;
}) {
  const [text, setText] = useState(value ?? "");
  const [busy, setBusy] = useState(false);
  useEffect(() => setText(value ?? ""), [value]);
  const dirty = editable && text.trim() !== (value ?? "").trim();
  useEffect(() => onDirty(dirty), [dirty, onDirty]);
  if (!editable)
    return (
      <div>
        <h3>{label}</h3>
        {value ? <p style={{ whiteSpace: "pre-wrap" }}>{value}</p> : <p className="muted small">Chưa có.</p>}
      </div>
    );
  return (
    <form
      onSubmit={(e: FormEvent) => {
        e.preventDefault();
        setBusy(true);
        void onSave(text).finally(() => setBusy(false));
      }}
    >
      <label style={{ display: "flex", flexDirection: "column", alignItems: "stretch", gap: 4 }}>
        <strong>{label}</strong>
        <span className="small muted">{hint}</span>
        <textarea value={text} onChange={(e) => setText(e.target.value)} rows={5} maxLength={10_000} />
      </label>
      <div className="toolbar" style={{ marginTop: 6 }}>
        <button type="submit" className="sm" disabled={busy || !dirty}>
          Lưu
        </button>
        {dirty && <span className="small muted">Chưa lưu</span>}
      </div>
    </form>
  );
}

export default function EvidencePage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const [month, setMonth] = useState<string | null>(null);
  useEffect(() => {
    let wanted = thisMonth();
    try {
      const m = new URLSearchParams(window.location.search).get("month");
      if (m && /^\d{4}-\d{2}$/.test(m) && m <= thisMonth() && monthsBetween(m, thisMonth()) <= MAX_BACK) wanted = m;
    } catch {
      /* keep this month */
    }
    setMonth(wanted);
  }, []);
  const go = (m: string) => {
    setMonth(m);
    try {
      const url = new URL(window.location.href);
      url.searchParams.set("month", m);
      window.history.replaceState(null, "", url);
    } catch {
      /* the month just is not kept in the URL */
    }
  };
  const key = month && `/evidence/${id}/${month}`;
  // While an AI draft is being written (a background job), poll until it is in.
  const { data, error, mutate } = useApi<EvidencePack>(key, {
    refreshInterval: (latest) => (latest?.ai_draft_running ? 4000 : 0),
  });
  const { mutate: mutateKey } = useSWRConfig();
  const [message, setMessage] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  // Unsaved notes block the state actions: submitting or closing would drop them.
  const dirty = useRef(new Set<string>());
  const [dirtyCount, setDirtyCount] = useState(0);
  const onDirty = useRef(
    new Map<string, (d: boolean) => void>(
      ["self_notes", "lead_notes", "goals_next"].map((f) => [
        f,
        (d: boolean) => {
          if (d) dirty.current.add(f);
          else dirty.current.delete(f);
          setDirtyCount(dirty.current.size);
        },
      ]),
    ),
  ).current;
  useEffect(() => {
    dirty.current.clear();
    setDirtyCount(0);
    setMessage(null);
  }, [month]);
  const shown = data && data.period === month ? data : undefined;

  async function run(period: string, action: () => Promise<EvidencePack>, done: string) {
    setBusy(true);
    try {
      const fresh = await action();
      await mutateKey(`/evidence/${id}/${fresh.period}`, fresh, { revalidate: false });
      setMessage(done);
    } catch (e) {
      if (e instanceof ApiError && e.status === 409) {
        await mutateKey(`/evidence/${id}/${period}`);
        setMessage("Lỗi: hồ sơ vừa đổi trạng thái (có thể do người khác cập nhật), đã tải lại. Kiểm tra rồi thử lại.");
      } else setMessage(`Lỗi: ${e instanceof ApiError ? e.message : String(e)}`);
    } finally {
      setBusy(false);
    }
  }
  const act = (period: string, action: string, done: string, confirmText?: string) => {
    if (confirmText && !window.confirm(confirmText)) return;
    void run(period, () => api.post<EvidencePack>(`/evidence/${id}/${period}/${action}`), done);
  };
  const saveNote = (period: string, field: "self_notes" | "lead_notes" | "goals_next") => (text: string) =>
    run(period, () => api.patch<EvidencePack>(`/evidence/${id}/${period}`, { [field]: text }), "Đã lưu.");

  const forbidden = error instanceof ApiError && error.status === 403;
  const notFound = error instanceof ApiError && error.status === 404;
  const badMonth = error instanceof ApiError && error.status === 422;
  const header = (
    <PageHeader
      eyebrow={<Link href={`/members/${id}`} className="muted">← Trang thành viên</Link>}
      title={data ? `Hồ sơ tháng · ${data.user.full_name ?? data.user.primary_email ?? `#${id}`}` : "Hồ sơ tháng"}
      subtitle="Cho buổi 1:1 và coaching. Không có điểm, không dùng làm căn cứ lương thưởng."
      actions={
        month &&
        !forbidden &&
        !notFound && (
          <Pager
            label={monthLabel(month)}
            onPrev={() => go(shift(month, -1))}
            onNext={() => go(shift(month, 1))}
            prevDisabled={monthsBetween(month, thisMonth()) >= MAX_BACK}
            nextDisabled={month >= thisMonth()}
          />
        )
      }
    />
  );
  if (forbidden)
    return (
      <>
        {header}
        <Card>
          <EmptyState icon="user" title="Bạn không xem được hồ sơ này">
            Hồ sơ tháng chỉ dành cho chính người đó, lead trực tiếp của họ và admin.
          </EmptyState>
        </Card>
      </>
    );
  if (notFound || badMonth)
    return (
      <>
        {header}
        <Card>
          <EmptyState icon="calendar" title={notFound ? "Không tìm thấy người này" : "Không mở được tháng này"}>
            {notFound ? (
              "Người dùng có thể đã bị xoá."
            ) : (
              <button type="button" className="sm" onClick={() => go(thisMonth())}>
                Về tháng này
              </button>
            )}
          </EmptyState>
        </Card>
      </>
    );
  if (!shown)
    return (
      <>
        {header}
        <Loading error={error} rows={5}>
          {error && (
            <button type="button" className="sm" onClick={() => void mutate()}>
              Thử lại
            </button>
          )}
        </Loading>
      </>
    );

  const pack = shown;
  const period = pack.period;
  const snap = pack.snapshot;
  const can = pack.can;
  const blocked = dirtyCount > 0;
  const byId = new Map(snap.merged.items.map((m) => [m.id, m]));
  const notableIds = new Set(snap.notable.map((n) => n.id));
  const step = STEPS.findIndex((s) => s.status === pack.status);
  // The one solid action of the state (omicx-ui): submit for the person, review/share for the lead.
  const primary: ReactNode = can.submit ? (
    <button className="primary" disabled={busy || blocked} onClick={() => act(period, "submit", "Đã gửi cho lead.", "Gửi hồ sơ cho lead? Số liệu sẽ được chốt tại thời điểm này; sau khi gửi bạn không sửa được phần tự nhận xét cho tới khi lead trả lại.")}>
      Gửi cho lead
    </button>
  ) : can.review ? (
    <button className="primary ink" disabled={busy || blocked} onClick={() => act(period, "review", "Đã đánh dấu đã xem.", "Đánh dấu đã xem? Sau bước này không trả hồ sơ về nháp được nữa; hai bên ghi mục tiêu trong 1:1.")}>
      Đã xem, sẵn sàng 1:1
    </button>
  ) : can.share ? (
    <button className="primary ink" disabled={busy || blocked} onClick={() => act(period, "share", "Đã chốt hồ sơ.", "Chốt hồ sơ sau buổi 1:1? Sau khi chốt, nhận xét của lead và mục tiêu không sửa được nữa.")}>
      Chốt sau 1:1
    </button>
  ) : null;

  return (
    <>
      {header}
      <Message text={message} onClose={() => setMessage(null)} />
      {(snap.unmapped_accounts?.length ?? 0) > 0 && (
        <Alert tone="info">
          Có tài khoản Git chưa gắn với ai cùng làm trong các repo này ({(snap.unmapped_accounts ?? []).slice(0, 5).join(", ")}
          {(snap.unmapped_accounts ?? []).length > 5 ? "…" : ""}). Nếu có tài khoản của người này, nhờ admin gắn (Admin › Tài khoản chưa
          gắn người) để việc của họ có đủ trong hồ sơ.
        </Alert>
      )}
      {snap.stale_repos.length > 0 && (
        <Alert tone="info">
          Dữ liệu có thể chưa đủ: {snap.stale_repos.length} repo chưa đồng bộ gần đây ({snap.stale_repos.join(", ")}).
        </Alert>
      )}

      <Card
        id="evidence-status"
        title="Trạng thái"
        subtitle={`Số liệu tính lúc ${dateTime(snap.built_at)}${pack.persisted ? "" : " (chưa lưu: hồ sơ được tạo khi bạn ghi chú hoặc gửi)"}`}
        collapsible={false}
      >
        <ol className="toolbar" role="list" style={{ listStyle: "none", padding: 0, margin: "0 0 12px", flexWrap: "wrap" }} aria-label="Các bước">
          {STEPS.map((s, i) => (
            <li key={s.status} aria-current={i === step ? "step" : undefined}>
              <Pill tone={i < step ? "good" : i === step ? "primary" : undefined} icon={i < step ? "check" : i === step ? "chevronRight" : undefined}>
                {i + 1}. {s.label}
                {i === step && <span className="visually-hidden"> (bước hiện tại)</span>}
              </Pill>
            </li>
          ))}
        </ol>
        <p className="small muted" style={{ margin: "0 0 12px" }}>
          {pack.submitted_at && <>Gửi {dateTime(pack.submitted_at)}. </>}
          {pack.reviewed_at && <>{pack.reviewed_by_name ?? "Lead"} xem {dateTime(pack.reviewed_at)}. </>}
          {pack.shared_at && <>Chốt {dateTime(pack.shared_at)}.</>}
        </p>
        <div className="toolbar">
          {primary}
          {can.refresh && (
            <button className="sm" disabled={busy} onClick={() => act(period, "refresh", "Đã làm mới số liệu.")}>
              Làm mới số liệu
            </button>
          )}
          {can.return && (
            <button className="sm" disabled={busy || blocked} onClick={() => act(period, "return", "Đã trả lại hồ sơ.", "Trả hồ sơ về nháp để người đó bổ sung?")}>
              Trả lại để bổ sung
            </button>
          )}
          {blocked && primary && <span className="small muted">Lưu ghi chú đang sửa trước khi chuyển bước.</span>}
        </div>
      </Card>

      <StatStrip label={`Tóm tắt ${monthLabel(period).toLowerCase()}`}>
        <StripStat label="MR đã merge" value={snap.merged.count} />
        <StripStat label="Review cho người khác" value={snap.reviews_given.count} sub={`${snap.reviews_given.authors} người`} />
        <StripStat label="MR chờ review lâu" title="Chờ phản hồi lâu hơn p75 của repo: vướng ở người khác" value={snap.waiting.length} />
      </StatStrip>

      <div className="grid two">
        <Card
          id="evidence-merged"
          title="Việc đã giao"
          subtitle={`MR đã merge trong tháng, đáng chú ý trước${snap.merged.items.length < snap.merged.count ? ` · hiển thị ${snap.merged.items.length}/${snap.merged.count}` : ""}`}
        >
          {snap.merged.count === 0 ? (
            <EmptyState title="Không có MR nào merge trong tháng">Việc ngoài Git ghi ở mục Bằng chứng bổ sung.</EmptyState>
          ) : (
            <ul className="finding-list">
              {snap.notable.map((n) => {
                const m = byId.get(n.id);
                return m ? <MrLine key={`n${n.id}`} m={m} why={n.why} /> : null;
              })}
              {snap.merged.items
                .filter((m) => !notableIds.has(m.id))
                .map((m) => (
                  <MrLine key={m.id} m={m} />
                ))}
            </ul>
          )}
        </Card>
        <Card
          id="evidence-helped"
          title="Hỗ trợ người khác"
          subtitle={`MR của đồng đội có phản hồi (comment hoặc approve) của người này${snap.reviews_given.items.length < snap.reviews_given.count ? ` · hiển thị ${snap.reviews_given.items.length}/${snap.reviews_given.count}` : ""}`}
        >
          {snap.reviews_given.count === 0 ? (
            <EmptyState title="Chưa review MR nào của người khác trong tháng" />
          ) : (
            <ul className="finding-list">
              {snap.reviews_given.items.map((m) => (
                <MrLine key={m.id} m={m} />
              ))}
            </ul>
          )}
        </Card>
      </div>

      <div className="grid two">
        <Card
          id="evidence-quality"
          title="Chất lượng"
          subtitle={`Lỗi critical/high đã được kiểm chứng (${snap.quality.fixed}/${snap.quality.confirmed_severe} đã sửa) · ${snap.quality.disputes_raised} khiếu nại, ${snap.quality.disputes_accepted} được chấp nhận. Để trao đổi, không phải điểm trừ.`}
        >
          {snap.quality.items.length === 0 ? (
            <EmptyState icon="checkCircle" tone="good" title="Không có lỗi nghiêm trọng nào được xác nhận" />
          ) : (
            <ul className="finding-list">
              {snap.quality.items.map((f) => (
                <li key={f.id}>
                  <SeverityBadge severity={f.severity} />
                  <div className="body">
                    <Link href={`/reviews/${f.review_id}`}>{f.title ?? "Finding"}</Link>{" "}
                    {f.status === "fixed" && <Pill tone="good">Đã sửa</Pill>}
                    <div className="meta">
                      {CATEGORY_LABEL[f.category] ?? f.category}
                      {f.file_path && (
                        <>
                          {" "}· <code>{f.file_path}{f.line_start ? `:${f.line_start}` : ""}</code>
                        </>
                      )}
                    </div>
                  </div>
                </li>
              ))}
            </ul>
          )}
        </Card>
        <Card id="evidence-waiting" title="Vướng mắc" subtitle="MR chờ phản hồi lâu hơn thường lệ của repo (không phải lỗi của tác giả)">
          {snap.waiting.length === 0 ? (
            <EmptyState title="Không có MR nào phải chờ lâu bất thường" />
          ) : (
            <ul className="finding-list">
              {snap.waiting.map((w) => {
                const m = byId.get(w.id);
                return (
                  <li key={w.id}>
                    <div className="body">
                      {m ? (m.web_url ? <ExternalLink href={m.web_url}>{`!${m.iid} ${m.title ?? ""}`}</ExternalLink> : `!${m.iid} ${m.title ?? ""}`) : "Một MR ngoài danh sách hiển thị"}
                      <div className="meta">
                        chờ review {hours(w.pickup)} · thường lệ của repo (p75) {hours(w.repo_p75)}
                      </div>
                    </div>
                  </li>
                );
              })}
            </ul>
          )}
        </Card>
      </div>

      {(snap.direct_commits?.count ?? 0) > 0 && (
        <Card
          id="evidence-direct"
          title="Commit không qua MR"
          subtitle={`${snap.direct_commits?.count ?? 0} commit đẩy thẳng lên nhánh, không có review qua MR${(snap.direct_commits?.items.length ?? 0) < (snap.direct_commits?.count ?? 0) ? ` · hiển thị ${snap.direct_commits?.items.length}` : ""}`}
        >
          <ul className="finding-list">
            {(snap.direct_commits?.items ?? []).map((c) => (
              <li key={c.sha}>
                <div className="body">
                  {c.web_url ? <ExternalLink href={c.web_url}>{c.title ?? c.sha.slice(0, 8)}</ExternalLink> : (c.title ?? c.sha.slice(0, 8))}
                  <div className="meta">
                    {c.path_with_namespace} · <code>{c.sha.slice(0, 8)}</code> · {dateTime(c.authored_at)}
                  </div>
                </div>
              </li>
            ))}
          </ul>
        </Card>
      )}

      <Items key={`items-${period}`} pack={pack} busy={busy} run={(action, done) => run(period, action, done)} id={id} month={period} />

      <Card key={`notes-${period}`} id="evidence-notes" title="Trao đổi" subtitle="Tự nhận xét → nhận xét của lead → mục tiêu tháng sau chốt trong 1:1">
        <div className="stack">
          <NoteBox
            label="Tự nhận xét"
            hint="Việc đã làm (kể cả ngoài Git), điều muốn được ghi nhận, vướng mắc. Lưu trước khi gửi cho lead."
            value={pack.self_notes}
            editable={can.edit_self_notes}
            onSave={saveNote(period, "self_notes")}
            onDirty={onDirty.get("self_notes") ?? (() => undefined)}
          />
          {can.reply && (
            <ReplyBox
              busy={busy}
              onSend={(text) =>
                run(period, () => api.patch<EvidencePack>(`/evidence/${id}/${period}`, { reply: text }), "Đã thêm phản hồi.")
              }
            />
          )}
          {(can.ai_draft || pack.ai_draft) && (
            <AiDraft
              draft={pack.ai_draft}
              canRun={can.ai_draft}
              canUse={can.edit_lead_notes}
              running={pack.ai_draft_running}
              hasNotes={!!pack.lead_notes}
              busy={busy}
              onRun={() =>
                run(period, () => api.post<EvidencePack>(`/evidence/${id}/${period}/ai-draft`), "Đang tạo bản nháp (thường dưới hai phút); trang tự cập nhật.")
              }
              onUse={(text) =>
                run(period, () => api.patch<EvidencePack>(`/evidence/${id}/${period}`, { lead_notes: text }), "Đã chép bản nháp vào nhận xét của lead.")
              }
            />
          )}
          <NoteBox
            key={`lead-${pack.lead_notes ?? ""}`}
            label="Nhận xét của lead"
            hint="Ghi nhận và góp ý cụ thể, dẫn tới MR/review làm bằng chứng."
            value={pack.lead_notes}
            editable={can.edit_lead_notes}
            onSave={saveNote(period, "lead_notes")}
            onDirty={onDirty.get("lead_notes") ?? (() => undefined)}
          />
          <NoteBox
            label="Mục tiêu tháng sau"
            hint="Hai bên thống nhất trong buổi 1:1."
            value={pack.goals_next}
            editable={can.edit_goals}
            onSave={saveNote(period, "goals_next")}
            onDirty={onDirty.get("goals_next") ?? (() => undefined)}
          />
        </div>
      </Card>
    </>
  );
}

/** §6.4: a draft from the frozen snapshot, every line cites its evidence; the lead's only. */
function AiDraft({
  draft,
  canRun,
  canUse,
  running,
  hasNotes,
  busy,
  onRun,
  onUse,
}: {
  draft: string | null;
  canRun: boolean;
  canUse: boolean;
  running: boolean;
  hasNotes: boolean;
  busy: boolean;
  onRun: () => Promise<void>;
  onUse: (text: string) => Promise<void>;
}) {
  return (
    <div className="card" style={{ background: "var(--surface-2)" }}>
      <div className="toolbar" style={{ justifyContent: "space-between", flexWrap: "wrap" }}>
        <strong>Bản nháp AI (chỉ lead thấy)</strong>
        <span className="toolbar">
          {canRun && (
            <button
              type="button"
              className="sm"
              disabled={busy || running}
              onClick={() => {
                if (window.confirm("Tạo bản nháp nhận xét từ số liệu đã chốt? Chỉ số liệu và tiêu đề MR được gửi cho Claude, không gửi mã nguồn; tốn một ít ngân sách LLM của tháng."))
                  void onRun();
              }}
            >
              {running ? "Đang tạo…" : draft ? "Tạo lại" : "Tạo bản nháp"}
            </button>
          )}
          {draft && canUse && (
            <button
              type="button"
              className="sm ghost"
              disabled={busy}
              onClick={() => {
                if (!hasNotes || window.confirm("Thay nhận xét của lead đang có bằng bản nháp này?")) void onUse(draft);
              }}
            >
              Dùng làm nhận xét
            </button>
          )}
        </span>
      </div>
      {running && (
        <p className="small muted" role="status" style={{ margin: "8px 0 0" }}>
          Đang tạo bản nháp từ số liệu đã chốt… trang tự cập nhật khi xong.
        </p>
      )}
      {draft ? (
        <p className="small" style={{ whiteSpace: "pre-wrap", margin: "8px 0 0" }}>
          {draft}
        </p>
      ) : (
        <p className="small muted" style={{ margin: "8px 0 0" }}>
          Mỗi câu trong bản nháp dẫn tới MR, finding hay bằng chứng trong hồ sơ; câu không dẫn được bị bỏ. Bản nháp không tự gửi cho ai.
        </p>
      )}
    </div>
  );
}

/** After the 1:1 the person may still answer; the answer is appended with its date. */
function ReplyBox({ busy, onSend }: { busy: boolean; onSend: (text: string) => Promise<void> }) {
  const [text, setText] = useState("");
  return (
    <form
      onSubmit={(e: FormEvent) => {
        e.preventDefault();
        void onSend(text.trim()).then(() => setText(""));
      }}
    >
      <label style={{ display: "flex", flexDirection: "column", alignItems: "stretch", gap: 4 }}>
        <strong>Phản hồi sau 1:1</strong>
        <span className="small muted">Được nối vào cuối phần tự nhận xét kèm ngày; phần đã có không sửa được.</span>
        <textarea value={text} onChange={(e) => setText(e.target.value)} rows={3} maxLength={4000} />
      </label>
      <div className="toolbar" style={{ marginTop: 6 }}>
        <button type="submit" className="sm" disabled={busy || !text.trim()}>
          Thêm phản hồi
        </button>
      </div>
    </form>
  );
}

function Items({
  pack,
  busy,
  run,
  id,
  month,
}: {
  pack: EvidencePack;
  busy: boolean;
  run: (action: () => Promise<EvidencePack>, done: string) => Promise<void>;
  id: string;
  month: string;
}) {
  const [kind, setKind] = useState<EvidenceKind>("other");
  const [title, setTitle] = useState("");
  const [ref, setRef] = useState("");
  const [note, setNote] = useState("");
  const add = (e: FormEvent) => {
    e.preventDefault();
    void run(async () => {
      const saved = await api.post<EvidencePack>(`/evidence/${id}/${month}/items`, {
        kind,
        title,
        ref: ref || null,
        note: note || null,
      });
      setTitle("");
      setRef("");
      setNote("");
      return saved;
    }, "Đã thêm bằng chứng.");
  };
  return (
    <Card id="evidence-items" title="Bằng chứng bổ sung" subtitle="Việc không thấy trên Git: xử lý sự cố, thiết kế, hướng dẫn đồng đội…">
      {pack.items.length === 0 ? (
        <p className="muted small">Chưa có.</p>
      ) : (
        <ul className="finding-list">
          {pack.items.map((i) => (
            <li key={i.id}>
              <Pill>{KIND_LABEL[i.kind]}</Pill>
              <div className="body">
                {i.ref && /^https?:\/\//i.test(i.ref) ? <ExternalLink href={i.ref}>{i.title}</ExternalLink> : i.title}
                {i.note && <div className="small" style={{ whiteSpace: "pre-wrap" }}>{i.note}</div>}
                <div className="meta">{i.added_by_name ? `thêm bởi ${i.added_by_name}` : ""}</div>
              </div>
              {i.mine && pack.can.add_item && (
                <button
                  type="button"
                  className="sm ghost danger"
                  disabled={busy}
                  onClick={() => {
                    if (window.confirm(`Xoá "${i.title}"?`))
                      void run(() => api.delete<EvidencePack>(`/evidence/${id}/${month}/items/${i.id}`), "Đã xoá.");
                  }}
                >
                  Xoá
                </button>
              )}
            </li>
          ))}
        </ul>
      )}
      {pack.can.add_item && (
        <form className="toolbar" onSubmit={add} style={{ marginTop: 12, flexWrap: "wrap" }}>
          <select value={kind} onChange={(e) => setKind(e.target.value as EvidenceKind)} aria-label="Loại bằng chứng">
            {(Object.keys(KIND_LABEL) as EvidenceKind[]).map((k) => (
              <option key={k} value={k}>
                {KIND_LABEL[k]}
              </option>
            ))}
          </select>
          <input value={title} onChange={(e) => setTitle(e.target.value)} placeholder="Mô tả ngắn" required maxLength={300} aria-label="Mô tả" />
          <input value={ref} onChange={(e) => setRef(e.target.value)} placeholder="Link (tuỳ chọn)" maxLength={1000} aria-label="Link" />
          <input value={note} onChange={(e) => setNote(e.target.value)} placeholder="Ghi chú (tuỳ chọn)" maxLength={2000} aria-label="Ghi chú" />
          <button type="submit" className="sm" disabled={busy || !title.trim()}>
            Thêm
          </button>
        </form>
      )}
    </Card>
  );
}
