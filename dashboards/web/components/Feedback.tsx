"use client";

import { useEffect, useState } from "react";

import { DocMarkdown } from "@/components/DocMarkdown";
import { StatusMenu } from "@/components/FeedbackStatusMenu";
import { Icon, IconButton } from "@/components/icons";
import { Alert, Avatar, Drawer, EmptyState, Loading, Message, Pill } from "@/components/ui";
import { ApiError, api, useApi } from "@/lib/api";
import {
  type FeedbackDetail,
  type FeedbackExportRow,
  type FeedbackKind,
  type FeedbackMeta,
  type FeedbackPerson,
  type FeedbackRow,
  type FeedbackStatus,
  KIND_ICON,
  KIND_LABEL,
  PRIORITY_TONE,
  STATUS_LABEL,
  STATUS_TONE,
  EXPORTS_KEY,
  download,
  isAnalysing,
  itemMdUrl,
  setFeedbackStatus,
} from "@/lib/feedback";
import { dateTimeMin, relative } from "@/lib/format";

/*
 * Phản ánh & Đề xuất (docs/specs/32 §7.1): the form everyone sends a report with, their own
 * reports and what became of them, and the one report's view shared with the managers' board
 * (author, AI analysis as Markdown, the author's own words, triage).
 */

const errorText = (e: unknown) => (e instanceof ApiError ? e.message : String(e));

const KIND_HINT: Record<FeedbackKind, string> = {
  bug: "Một thứ đang chạy sai",
  feature: "Một thứ chưa có",
  improvement: "Làm tốt hơn thứ đã có",
  question: "Chưa rõ cách dùng",
};

const BODY_HINT: Record<FeedbackKind, string> = {
  bug: "Các bước làm: …\nKết quả thấy: …\nKết quả mong đợi: …",
  feature: "Hiện tại: tôi phải …\nMong muốn: …\nVì sao quan trọng: (tiết kiệm bao lâu, ai cần)",
  improvement: "Chỗ đang khó dùng: …\nNên đổi thành: …\nVì sao: …",
  question: "Tôi muốn làm: …\nĐã thử: …\nChưa rõ: …",
};

export function KindPill({ kind }: { kind: FeedbackKind }) {
  return (
    <span className={`fbk-kind-tag ${kind}`}>
      <Icon name={KIND_ICON[kind]} size={12} />
      {KIND_LABEL[kind]}
    </span>
  );
}

export function StatusPill({ status }: { status: FeedbackStatus }) {
  return <Pill tone={STATUS_TONE[status]}>{STATUS_LABEL[status]}</Pill>;
}

export function AnalysisBadge({ row }: { row: Pick<FeedbackRow, "analysis_status" | "analysing"> }) {
  if (isAnalysing(row))
    return (
      <span className="fbk-analysing small" title="AI đang phân tích">
        <span className="spinner" aria-hidden="true" /> AI đang đọc
      </span>
    );
  if (row.analysis_status === "failed")
    return (
      <span className="fbk-failed small" title="Chưa phân tích được">
        <Icon name="alert" size={12} /> chưa phân tích
      </span>
    );
  return null;
}

function AuthorLine({ person, at, page }: { person: FeedbackPerson | null; at: string; page?: string | null }) {
  return (
    <div className="fbk-author">
      <Avatar name={person?.name ?? "?"} src={person?.avatar_url} />
      <div className="fbk-author-text">
        <strong>{person?.name ?? "(không rõ)"}</strong>
        <span className="muted small">
          {person?.teams.length ? `${person.teams.join(", ")} · ` : ""}
          <time dateTime={at} title={dateTimeMin(at)}>
            gửi {relative(at)}
          </time>
          {page ? (
            <>
              {" · trang "}
              <code>{page}</code>
            </>
          ) : null}
        </span>
      </div>
    </div>
  );
}

/* ---------- the form ---------- */

export function FeedbackForm({
  meta,
  from,
  onSent,
}: {
  meta: FeedbackMeta;
  from: string | null;
  onSent: (item: FeedbackDetail) => void;
}) {
  const [kind, setKind] = useState<FeedbackKind>("feature");
  const [area, setArea] = useState(meta.suggested_area);
  const [title, setTitle] = useState("");
  const [body, setBody] = useState("");
  const [page, setPage] = useState(from ?? "");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => setArea(meta.suggested_area), [meta.suggested_area]);

  const ready = title.trim().length >= 3 && body.trim().length >= 10;
  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!ready || busy) return;
    setBusy(true);
    setError(null);
    try {
      const item = await api.post<FeedbackDetail>("/feedback", {
        kind,
        area,
        title: title.trim(),
        body: body.trim(),
        page_url: page.trim() || null,
      });
      setTitle("");
      setBody("");
      onSent(item);
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <form className="card fbk-form" onSubmit={submit} aria-labelledby="fbk-form-title">
      <div>
        <h2 id="fbk-form-title">Gửi phản ánh</h2>
        <p className="muted small">
          Viết như đang nói với người làm ra CodePulse. AI sẽ phân loại, tóm tắt và gom các ý giống nhau; bạn theo dõi kết quả ở bên cạnh.
        </p>
      </div>
      <fieldset className="fbk-kinds">
        <legend className="small muted">Loại</legend>
        {(Object.keys(KIND_LABEL) as FeedbackKind[]).map((k) => (
          <label key={k} className={`fbk-kind${kind === k ? " active" : ""}`}>
            <input type="radio" name="fbk-kind" value={k} checked={kind === k} onChange={() => setKind(k)} />
            <span className={`fbk-kind-icon ${k}`}>
              <Icon name={KIND_ICON[k]} size={16} />
            </span>
            <span className="fbk-kind-text">
              <strong>{KIND_LABEL[k]}</strong>
              <span className="muted small">{KIND_HINT[k]}</span>
            </span>
          </label>
        ))}
      </fieldset>
      <div className="fbk-form-grid">
        <label className="adm-field">
          <span className="small muted">Lĩnh vực</span>
          <select value={area} onChange={(e) => setArea(e.target.value)}>
            {meta.areas.map((a) => (
              <option key={a.key} value={a.key}>
                {a.label}
              </option>
            ))}
          </select>
        </label>
        <label className="adm-field">
          <span className="small muted">Trang liên quan</span>
          <input value={page} onChange={(e) => setPage(e.target.value)} placeholder="/tasks/board" maxLength={500} />
        </label>
      </div>
      <label className="adm-field">
        <span className="small muted">Tiêu đề</span>
        <input
          value={title}
          onChange={(e) => setTitle(e.target.value)}
          placeholder={kind === "bug" ? "Ví dụ: Board không lưu khi kéo task sang cột Done" : "Ví dụ: Lọc board theo sprint"}
          maxLength={meta.title_max}
          required
        />
      </label>
      <label className="adm-field">
        <span className="fbk-label-row small muted">
          <span>Mô tả</span>
          <span>
            {body.length.toLocaleString("vi-VN")}/{meta.body_max.toLocaleString("vi-VN")}
          </span>
        </span>
        <textarea
          value={body}
          onChange={(e) => setBody(e.target.value)}
          placeholder={BODY_HINT[kind]}
          rows={7}
          maxLength={meta.body_max}
          required
        />
      </label>
      {error && <Alert tone="error">{error}</Alert>}
      <div className="fbk-form-foot">
        <span className="muted small">Tên bạn được ghi kèm phản ánh. Tối đa {meta.per_day} phản ánh mỗi ngày.</span>
        <button type="submit" className="primary" disabled={!ready || busy}>
          <Icon name="arrowUp" size={14} /> {busy ? "Đang gửi…" : "Gửi phản ánh"}
        </button>
      </div>
    </form>
  );
}

/* ---------- one report ---------- */

export function FeedbackView({
  id,
  meta,
  features,
  onChanged,
  onDeleted,
}: {
  id: number;
  meta: FeedbackMeta;
  /** Features already on the tree, offered when renaming (managers). */
  features?: string[];
  onChanged?: () => void;
  onDeleted?: () => void;
}) {
  const { data: item, error, mutate } = useApi<FeedbackDetail>(`/feedback/${id}`, {
    refreshInterval: (latest) => (latest?.analysing ? 3000 : 0),
  });
  // Managers: the export history (same SWR key as the board's), so "đã export N lần" here and
  // "Lịch sử export (N)" there read one list and cannot disagree.
  const history = useApi<FeedbackExportRow[]>(item?.can_manage ? EXPORTS_KEY : null);
  const [note, setNote] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [editing, setEditing] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [wasAnalysing, setWasAnalysing] = useState(false);
  useEffect(() => {
    setNote(null);
    setEditing(false);
    setConfirmDelete(false);
  }, [id]);
  // The list's status follows when the analysis finishes.
  useEffect(() => {
    if (!item) return;
    if (wasAnalysing && !item.analysing) onChanged?.();
    setWasAnalysing(item.analysing);
  }, [item?.analysing]); // eslint-disable-line react-hooks/exhaustive-deps

  if (error) return <Alert tone="error">{error.message}</Alert>;
  if (!item) return <Loading rows={6} />;

  const act = async (fn: () => Promise<unknown>, ok: string) => {
    setBusy(true);
    setNote(null);
    try {
      await fn();
      await mutate();
      onChanged?.();
      setNote(ok);
    } catch (err) {
      setNote(`Lỗi: ${errorText(err)}`);
    } finally {
      setBusy(false);
    }
  };
  const remove = async () => {
    setBusy(true);
    try {
      await api.delete(`/feedback/${item.id}`);
      onDeleted?.();
    } catch (err) {
      setNote(`Lỗi: ${errorText(err)}`);
      setBusy(false);
    }
  };
  const areaLabel = meta.areas.find((a) => a.key === item.area)?.label ?? item.area;
  const canReanalyse = item.can_manage || item.can_edit;
  const batches = history.data?.filter((r) => r.item_ids.includes(item.id));
  const exported = batches ? batches.length : item.exported;
  const exportTip = batches?.length
    ? `Nằm trong ${batches.length} gói: ${batches.map((b) => `“${b.title}”`).join(", ")}`
    : item.exported_at
      ? `Lần cuối ${dateTimeMin(item.exported_at)}`
      : undefined;

  return (
    <article className="fbk-view" aria-labelledby={`fbk-title-${item.id}`}>
      <div className="fbk-view-tags">
        <span className="fbk-id">#{item.id}</span>
        <KindPill kind={item.kind} />
        {item.can_manage ? (
          <StatusMenu
            variant="chip"
            current={item.status}
            disabled={busy}
            onPick={(to) => void act(() => setFeedbackStatus([item.id], to), `Đã chuyển sang “${STATUS_LABEL[to]}”.`)}
          />
        ) : (
          <StatusPill status={item.status} />
        )}
        {item.priority && <Pill tone={PRIORITY_TONE[item.priority]}>{item.priority.toUpperCase()}</Pill>}
        {exported > 0 && (
          <Pill icon="download" title={exportTip}>
            Đã export {exported > 1 ? `${exported} lần` : ""}
          </Pill>
        )}
        <span className="fbk-view-actions">
          <IconButton icon="download" label="Tải .md của phản ánh này" onClick={() => download(itemMdUrl(item.id))} />
          {canReanalyse && (
            <IconButton
              icon="sparkle"
              label="Phân tích lại bằng AI"
              disabled={busy || item.analysing}
              onClick={() => void act(() => api.post(`/feedback/${item.id}/analyze`), "Đã gửi AI phân tích lại.")}
            />
          )}
          {item.can_edit && <IconButton icon="edit" label="Sửa nội dung" disabled={busy} onClick={() => setEditing(true)} />}
          {(item.can_edit || item.can_manage) && (
            <IconButton icon="remove" label="Xoá phản ánh" tone="danger" disabled={busy} onClick={() => setConfirmDelete(true)} />
          )}
        </span>
      </div>
      <h2 id={`fbk-title-${item.id}`} className="fbk-view-title">
        {item.title}
      </h2>
      <div className="fbk-crumb small muted">
        {areaLabel} <Icon name="chevronRight" size={12} /> {item.feature}
      </div>
      <AuthorLine person={item.author} at={item.created_at} page={item.page_url} />

      {confirmDelete && (
        <Alert tone="warning">
          Xoá phản ánh #{item.id}? Không khôi phục được.{" "}
          <button type="button" className="sm danger" disabled={busy} onClick={() => void remove()}>
            Xoá
          </button>{" "}
          <button type="button" className="sm ghost" disabled={busy} onClick={() => setConfirmDelete(false)}>
            Thôi
          </button>
        </Alert>
      )}
      <Message text={note} onClose={() => setNote(null)} />

      {item.triage_note && (
        <div className="fbk-reply">
          <div className="small muted">
            <Icon name="checkCircle" size={12} /> Phản hồi của người duyệt
            {item.triaged_by ? ` · ${item.triaged_by.name}` : ""}
            {item.triaged_at ? ` · ${relative(item.triaged_at)}` : ""}
          </div>
          <p>{item.triage_note}</p>
        </div>
      )}

      {editing ? (
        <EditForm item={item} meta={meta} busy={busy} onCancel={() => setEditing(false)} onSave={(body) => act(async () => {
          await api.patch(`/feedback/${item.id}`, body);
          setEditing(false);
        }, "Đã lưu; AI đang đọc lại.")} />
      ) : (
        <>
          <section className="fbk-analysis" aria-label="Phân tích của AI">
            <div className="fbk-section-head">
              <span className="eyebrow">
                <Icon name="sparkle" size={12} /> Phân tích của AI
              </span>
              <code className="fbk-path" title="Đường dẫn trong cây phản ánh">
                {item.path}
              </code>
            </div>
            {item.analysing ? (
              <Loading>AI đang phân tích phản ánh…</Loading>
            ) : item.analysis_status === "failed" ? (
              <Alert tone="warning">{item.error || "Chưa phân tích được."} Nội dung gốc vẫn được giữ và export được.</Alert>
            ) : (
              <>
                {item.summary && <p className="fbk-summary">{item.summary}</p>}
                {item.analysis_md ? <DocMarkdown text={item.analysis_md} className="fbk-md" /> : null}
                {item.result?.duplicates?.length ? (
                  <p className="small muted">Có thể trùng: {item.result.duplicates.map((d) => `#${d}`).join(", ")}</p>
                ) : null}
              </>
            )}
          </section>
          <details className="fbk-raw" open={item.analysis_status !== "ready"}>
            <summary className="small">Nội dung gốc của tác giả</summary>
            <p className="fbk-raw-title">
              <strong>{item.raw_title}</strong>{" "}
              <span className="muted small">
                ({KIND_LABEL[item.author_kind]} · {meta.areas.find((a) => a.key === item.author_area)?.label})
              </span>
            </p>
            <div className="fbk-raw-body">{item.body}</div>
          </details>
        </>
      )}

      {item.can_manage && <Triage key={`${item.id}-${item.triaged_at}`} item={item} meta={meta} features={features ?? []} busy={busy} onSave={(body) => act(() => api.patch(`/feedback/${item.id}`, body), "Đã lưu phân loại.")} />}
    </article>
  );
}

function EditForm({
  item,
  meta,
  busy,
  onCancel,
  onSave,
}: {
  item: FeedbackDetail;
  meta: FeedbackMeta;
  busy: boolean;
  onCancel: () => void;
  onSave: (body: Record<string, unknown>) => void;
}) {
  const [title, setTitle] = useState(item.raw_title);
  const [body, setBody] = useState(item.body);
  const [kind, setKind] = useState<FeedbackKind>(item.author_kind);
  const [area, setArea] = useState(item.author_area);
  return (
    <div className="fbk-edit">
      <div className="fbk-form-grid">
        <label className="adm-field">
          <span className="small muted">Loại</span>
          <select value={kind} onChange={(e) => setKind(e.target.value as FeedbackKind)}>
            {(Object.keys(KIND_LABEL) as FeedbackKind[]).map((k) => (
              <option key={k} value={k}>
                {KIND_LABEL[k]}
              </option>
            ))}
          </select>
        </label>
        <label className="adm-field">
          <span className="small muted">Lĩnh vực</span>
          <select value={area} onChange={(e) => setArea(e.target.value)}>
            {meta.areas.map((a) => (
              <option key={a.key} value={a.key}>
                {a.label}
              </option>
            ))}
          </select>
        </label>
      </div>
      <label className="adm-field">
        <span className="small muted">Tiêu đề</span>
        <input value={title} onChange={(e) => setTitle(e.target.value)} maxLength={meta.title_max} />
      </label>
      <label className="adm-field">
        <span className="small muted">Mô tả</span>
        <textarea value={body} onChange={(e) => setBody(e.target.value)} rows={8} maxLength={meta.body_max} />
      </label>
      <div className="fbk-form-foot">
        <button type="button" className="ghost" onClick={onCancel} disabled={busy}>
          Huỷ
        </button>
        <button
          type="button"
          className="primary"
          disabled={busy || title.trim().length < 3 || body.trim().length < 10}
          onClick={() => onSave({ title, body, kind, area })}
        >
          Lưu và cho AI đọc lại
        </button>
      </div>
    </div>
  );
}

function Triage({
  item,
  meta,
  features,
  busy,
  onSave,
}: {
  item: FeedbackDetail;
  meta: FeedbackMeta;
  features: string[];
  busy: boolean;
  onSave: (body: Record<string, unknown>) => void;
}) {
  const [area, setArea] = useState(item.area);
  const [feature, setFeature] = useState(item.feature === "Chưa phân loại" ? "" : item.feature);
  const [priority, setPriority] = useState(item.priority ?? "");
  const [status, setStatus] = useState<FeedbackStatus>(item.status);
  const [note, setNote] = useState(item.triage_note ?? "");
  const listId = `fbk-features-${item.id}`;
  const changed =
    area !== item.area ||
    feature !== (item.feature === "Chưa phân loại" ? "" : item.feature) ||
    priority !== (item.priority ?? "") ||
    status !== item.status ||
    note !== (item.triage_note ?? "");
  return (
    <section className="fbk-triage" aria-label="Phân loại và xử lý">
      <div className="eyebrow">Phân loại & xử lý</div>
      <div className="fbk-triage-grid">
        <label className="adm-field">
          <span className="small muted">Lĩnh vực</span>
          <select value={area} onChange={(e) => setArea(e.target.value)}>
            {meta.areas.map((a) => (
              <option key={a.key} value={a.key}>
                {a.label}
              </option>
            ))}
          </select>
        </label>
        <label className="adm-field">
          <span className="small muted">Chức năng</span>
          <input value={feature} onChange={(e) => setFeature(e.target.value)} list={listId} maxLength={60} placeholder="Board, Bộ lọc…" />
          <datalist id={listId}>
            {features.map((f) => (
              <option key={f} value={f} />
            ))}
          </datalist>
        </label>
        <label className="adm-field">
          <span className="small muted">Ưu tiên</span>
          <select value={priority} onChange={(e) => setPriority(e.target.value)}>
            {!item.priority && <option value="">—</option>}
            {meta.priorities.map((p) => (
              <option key={p.key} value={p.key}>
                {p.label}
              </option>
            ))}
          </select>
        </label>
        <label className="adm-field">
          <span className="small muted">Trạng thái</span>
          <select value={status} onChange={(e) => setStatus(e.target.value as FeedbackStatus)}>
            {meta.statuses.map((s) => (
              <option key={s.key} value={s.key}>
                {s.label}
              </option>
            ))}
          </select>
        </label>
      </div>
      <label className="adm-field">
        <span className="small muted">Ghi chú (tác giả thấy, đi kèm file export)</span>
        <textarea value={note} onChange={(e) => setNote(e.target.value)} rows={2} maxLength={4000} placeholder="Ví dụ: Sẽ làm trong sprint 12, gộp với #31." />
      </label>
      <div className="fbk-form-foot">
        <span className="muted small">{item.triaged_by ? `Lần cuối: ${item.triaged_by.name}, ${relative(item.triaged_at ?? item.created_at)}` : ""}</span>
        <button
          type="button"
          className="primary sm"
          disabled={busy || !changed}
          onClick={() =>
            onSave({
              ai_area: area,
              feature: feature.trim() || null,
              ...(priority ? { priority } : {}),
              status,
              triage_note: note,
            })
          }
        >
          Lưu phân loại
        </button>
      </div>
    </section>
  );
}

/* ---------- "Của tôi" ---------- */

export function MyFeedback({ meta, from, item }: { meta: FeedbackMeta; from: string | null; item?: string | null }) {
  const { data: rows, error, mutate } = useApi<FeedbackRow[]>("/feedback/mine", {
    refreshInterval: (latest) => (latest?.some(isAnalysing) ? 3000 : 0),
  });
  const [open, setOpen] = useState<number | null>(null);
  const [sent, setSent] = useState<string | null>(null);
  // ?item=12 (the bell's link when a manager moved the report) opens that report.
  useEffect(() => {
    if (item && /^\d+$/.test(item)) setOpen(Number(item));
  }, [item]);
  return (
    <div className="fbk-mine">
      <FeedbackForm
        meta={meta}
        from={from}
        onSent={(item) => {
          setSent(`Đã gửi #${item.id}. AI đang phân tích, kết quả hiện ngay trong danh sách.`);
          void mutate();
        }}
      />
      <section className="card fbk-mine-list" aria-labelledby="fbk-mine-title">
        <div className="fbk-section-head">
          <h2 id="fbk-mine-title">Phản ánh của tôi</h2>
          {rows && <span className="muted small">{rows.length}</span>}
        </div>
        <Message text={sent} onClose={() => setSent(null)} />
        {error ? (
          <Alert tone="error">{error.message}</Alert>
        ) : !rows ? (
          <Loading rows={4} />
        ) : rows.length === 0 ? (
          <EmptyState icon="message" title="Chưa có phản ánh nào">
            Gặp chỗ khó dùng hay có ý tưởng? Gửi ở form bên cạnh, mất chưa tới một phút.
          </EmptyState>
        ) : (
          <ul className="fbk-mine-rows">
            {rows.map((r) => (
              <li key={r.id}>
                <button type="button" className="fbk-mine-row" onClick={() => setOpen(r.id)}>
                  <span className="fbk-mine-top">
                    <KindPill kind={r.kind} />
                    <StatusPill status={r.status} />
                    <AnalysisBadge row={r} />
                    <span className="muted small fbk-push">{relative(r.created_at)}</span>
                  </span>
                  <span className="fbk-mine-title">{r.title}</span>
                  <span className="muted small">
                    {meta.areas.find((a) => a.key === r.area)?.label} ▸ {r.feature}
                    {r.priority ? ` · ${r.priority.toUpperCase()}` : ""}
                  </span>
                </button>
              </li>
            ))}
          </ul>
        )}
      </section>
      {open !== null && (
        <Drawer title={`Phản ánh #${open}`} onClose={() => setOpen(null)} width={720}>
          <FeedbackView
            id={open}
            meta={meta}
            onChanged={() => void mutate()}
            onDeleted={() => {
              setOpen(null);
              void mutate();
            }}
          />
        </Drawer>
      )}
    </div>
  );
}
