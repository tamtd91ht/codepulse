"use client";

import Link from "next/link";
import { type FormEvent, use, useMemo, useState } from "react";

import { ApprovalPill, BUILT_BY_LABEL, RefreshKnowledgeButton, SourceRef, StalePill } from "@/components/Knowledge";
import { Alert, Card, EmptyState, Loading, Message, PageHeader, Pill, SeverityBadge } from "@/components/ui";
import { ApiError, canEditKnowledge, knowledgeApi, knowledgePath, knowledgePoll, knowledgeStatsPath, useApi } from "@/lib/api";
import { SEVERITIES, SEVERITY_LABEL, dateTime, num } from "@/lib/format";
import type {
  Knowledge,
  KnowledgeEdit,
  KnowledgeInvariant,
  KnowledgeResponse,
  KnowledgeRuleStat,
  KnowledgeStats,
  KnowledgeVersion,
  KnowledgeVersionRow,
  Me,
  Repo,
  Severity,
} from "@/lib/types";

const MANUAL_INVARIANT_LIMIT = 10;

type Tab = "invariants" | "checks" | "domain" | "severity" | "conventions" | "index" | "omitted" | "conflicts" | "diff" | "stats";
const TABS: { id: Tab; label: string; count?: (k: Knowledge) => number }[] = [
  { id: "invariants", label: "Bất biến", count: (k) => k.invariants?.length ?? 0 },
  { id: "checks", label: "Checks", count: (k) => k.checks?.length ?? 0 },
  { id: "domain", label: "Nghiệp vụ" },
  { id: "severity", label: "Quy tắc severity", count: (k) => k.severity_rules?.length ?? 0 },
  { id: "conventions", label: "Convention", count: (k) => k.code_conventions?.length ?? 0 },
  { id: "index", label: "Mục lục", count: (k) => k.index?.length ?? 0 },
  { id: "omitted", label: "Đã bỏ", count: (k) => k.omitted?.length ?? 0 },
  { id: "conflicts", label: "Mâu thuẫn", count: (k) => k.conflicts?.length ?? 0 },
  { id: "diff", label: "So với bản trước" },
  { id: "stats", label: "Thống kê" },
];

const OMITTED_REASON: Record<string, string> = {
  tooling: "công cụ / tooling",
  output_format: "định dạng đầu ra",
  git_workflow: "quy trình git",
  style_linter: "style đã có linter",
  duplicate: "trùng nội dung",
  not_review: "không liên quan review",
};
const INDEX_SOURCE: Record<string, string> = { repo: "repo", shared: "dùng chung", code: "mã nguồn" };

const pct = (v: number | null | undefined) => (typeof v === "number" ? `${Math.round(v * 100)}%` : "—");
const who = (e: KnowledgeEdit) => `${e.user.full_name ?? `#${e.user.id}`} · ${dateTime(e.created_at)}`;

function Chips({ items, empty = "—" }: { items: string[] | undefined; empty?: string }) {
  if (!items?.length) return <span className="faint">{empty}</span>;
  return (
    <span className="chips" style={{ margin: 0, gap: 4 }}>
      {items.map((s) => (
        <span key={s} className="chip small">
          {s}
        </span>
      ))}
    </span>
  );
}

function parseSignals(text: string): string[] {
  return [...new Set(text.split(/[,\n]/).map((s) => s.trim()).filter(Boolean))];
}

/* ---------- Bất biến ---------- */

function InvariantRow({
  item,
  edits,
  canEdit,
  webUrl,
  sha,
  onEdit,
  onRemove,
}: {
  item: KnowledgeInvariant;
  edits: KnowledgeEdit[];
  canEdit: boolean;
  webUrl: string | null | undefined;
  sha: string | null | undefined;
  onEdit: (action: KnowledgeEdit["action"], payload: KnowledgeEdit["payload"]) => Promise<boolean>;
  onRemove: (edit: KnowledgeEdit) => Promise<boolean>;
}) {
  const [mode, setMode] = useState<"severity" | "reword" | null>(null);
  const [severity, setSeverity] = useState<Severity>(item.severity);
  const [statement, setStatement] = useState(item.statement);
  const [busy, setBusy] = useState(false);
  const disabled = edits.find((e) => e.action === "disable");
  const reworded = edits.find((e) => e.action === "reword");
  const resevered = edits.find((e) => e.action === "set_severity");
  const shownSeverity = resevered?.payload.severity ?? item.severity;
  const shownStatement = reworded?.payload.statement ?? item.statement;

  const submit = async (action: KnowledgeEdit["action"], payload: KnowledgeEdit["payload"]) => {
    setBusy(true);
    const ok = await onEdit(action, payload);
    setBusy(false);
    if (ok) setMode(null);
  };
  const remove = async (edit: KnowledgeEdit) => {
    setBusy(true);
    await onRemove(edit);
    setBusy(false);
  };

  return (
    <tr className={disabled ? "struck" : undefined}>
      <td data-label="Mã">
        <code>{item.id}</code>
        {item.authority === "shared" && <Pill tone="info">dùng chung</Pill>}
      </td>
      <td data-label="Phát biểu">
        {mode === "reword" ? (
          <form
            style={{ display: "grid", gap: 6 }}
            onSubmit={(e: FormEvent) => {
              e.preventDefault();
              if (statement.trim()) void submit("reword", { statement: statement.trim() });
            }}
          >
            <textarea rows={3} value={statement} onChange={(e) => setStatement(e.target.value)} aria-label="Lời mới" />
            <div className="toolbar" style={{ margin: 0 }}>
              <button type="submit" className="primary sm" disabled={busy || !statement.trim()}>
                Lưu
              </button>
              <button type="button" className="sm" onClick={() => setMode(null)} disabled={busy}>
                Huỷ
              </button>
            </div>
          </form>
        ) : (
          <>
            <span className="statement">{shownStatement}</span>
            {reworded && (
              <div className="small muted">
                <Pill tone="info">sửa lời</Pill> {who(reworded)} · gốc: <span title={item.statement}>{item.statement}</span>
                {canEdit && !disabled && (
                  <>
                    {" "}
                    <button type="button" className="link sm" disabled={busy} onClick={() => void remove(reworded)}>
                      bỏ sửa tay
                    </button>
                  </>
                )}
              </div>
            )}
            {disabled && (
              <div className="small muted">
                <Pill tone="bad">đã tắt</Pill> {who(disabled)}
              </div>
            )}
          </>
        )}
      </td>
      <td data-label="Severity">
        {mode === "severity" ? (
          <form
            className="toolbar"
            style={{ margin: 0 }}
            onSubmit={(e: FormEvent) => {
              e.preventDefault();
              void submit("set_severity", { severity });
            }}
          >
            <select className="compact" value={severity} onChange={(e) => setSeverity(e.target.value as Severity)} aria-label="Severity mới">
              {SEVERITIES.map((s) => (
                <option key={s} value={s}>
                  {SEVERITY_LABEL[s]}
                </option>
              ))}
            </select>
            <button type="submit" className="primary sm" disabled={busy}>
              Lưu
            </button>
            <button type="button" className="sm" onClick={() => setMode(null)} disabled={busy}>
              Huỷ
            </button>
          </form>
        ) : (
          <>
            <SeverityBadge severity={shownSeverity} />
            {resevered && (
              <div className="small muted">
                gốc {SEVERITY_LABEL[item.severity]} · {who(resevered)}
                {canEdit && !disabled && (
                  <>
                    {" "}
                    <button type="button" className="link sm" disabled={busy} onClick={() => void remove(resevered)}>
                      bỏ sửa tay
                    </button>
                  </>
                )}
              </div>
            )}
          </>
        )}
      </td>
      <td data-label="Tín hiệu">
        <Chips items={item.signals} />
      </td>
      <td data-label="Nguồn">
        <SourceRef source={item.source} webUrl={webUrl} sha={sha} />
      </td>
      <td className="num" data-label="Độ tin cậy">
        {pct(item.confidence)}
      </td>
      {canEdit && (
        <td data-label="Thao tác" style={{ whiteSpace: "nowrap" }}>
          {disabled ? (
            <button type="button" className="sm" disabled={busy} onClick={() => void remove(disabled)}>
              Bật lại
            </button>
          ) : (
            mode === null && (
              <div className="actions" style={{ gap: 4, justifyContent: "flex-start" }}>
                <button type="button" className="sm" disabled={busy} onClick={() => void submit("disable", {})}>
                  Tắt
                </button>
                <button type="button" className="sm" disabled={busy} onClick={() => setMode("severity")}>
                  Đổi severity
                </button>
                <button type="button" className="sm" disabled={busy} onClick={() => setMode("reword")}>
                  Sửa lời
                </button>
              </div>
            )
          )}
        </td>
      )}
    </tr>
  );
}

function AddInvariantForm({ count, onAdd }: { count: number; onAdd: (payload: KnowledgeEdit["payload"]) => Promise<boolean> }) {
  const [statement, setStatement] = useState("");
  const [severity, setSeverity] = useState<Severity>("high");
  const [signals, setSignals] = useState("");
  const [busy, setBusy] = useState(false);
  const full = count >= MANUAL_INVARIANT_LIMIT;
  return (
    <form
      className="add-forms"
      style={{ display: "grid", gap: 8 }}
      onSubmit={(e: FormEvent) => {
        e.preventDefault();
        if (full || !statement.trim()) return;
        setBusy(true);
        void onAdd({ statement: statement.trim(), severity, signals: parseSignals(signals) }).then((ok) => {
          setBusy(false);
          if (ok) {
            setStatement("");
            setSignals("");
          }
        });
      }}
    >
      <h4 style={{ margin: 0 }}>
        Thêm bất biến tay <span className="muted small">({count}/{MANUAL_INVARIANT_LIMIT})</span>
      </h4>
      <p className="muted small" style={{ margin: 0 }}>
        Bất biến tay chỉ CodePulse biết. Nên đưa vào <code>.claude/rules</code> của repo rồi bấm Rà soát lại để code và
        review dùng chung một nguồn; tối đa {MANUAL_INVARIANT_LIMIT} mục tay.
      </p>
      <textarea
        rows={2}
        value={statement}
        onChange={(e) => setStatement(e.target.value)}
        placeholder="Phát biểu, tối đa 2 dòng: ví dụ “Mọi truy vấn bảng invoices phải lọc theo tenant_id”"
        aria-label="Phát biểu"
        disabled={full}
      />
      <div className="toolbar" style={{ margin: 0 }}>
        <label>
          Severity khi vi phạm
          <select className="compact" value={severity} onChange={(e) => setSeverity(e.target.value as Severity)} disabled={full}>
            {SEVERITIES.map((s) => (
              <option key={s} value={s}>
                {SEVERITY_LABEL[s]}
              </option>
            ))}
          </select>
        </label>
        <input
          value={signals}
          onChange={(e) => setSignals(e.target.value)}
          placeholder="Tín hiệu, cách nhau bằng dấu phẩy: app/billing/**, db_query, money"
          aria-label="Tín hiệu"
          style={{ flex: 1, minWidth: 220 }}
          disabled={full}
        />
        <button type="submit" className="primary" disabled={busy || full || !statement.trim()}>
          {busy ? "Đang thêm…" : "Thêm"}
        </button>
      </div>
      {full && (
        <p className="muted small" style={{ margin: 0 }}>
          Đã đủ {MANUAL_INVARIANT_LIMIT} bất biến tay; xoá bớt hoặc đưa vào <code>.claude</code>.
        </p>
      )}
    </form>
  );
}

function InvariantsTab({
  version,
  edits,
  canEdit,
  webUrl,
  onEdit,
  onRemove,
}: {
  version: KnowledgeVersion;
  edits: KnowledgeEdit[];
  canEdit: boolean;
  webUrl: string | null | undefined;
  onEdit: (item_id: string, action: KnowledgeEdit["action"], payload: KnowledgeEdit["payload"]) => Promise<boolean>;
  onRemove: (edit: KnowledgeEdit) => Promise<boolean>;
}) {
  const items = version.data.invariants ?? [];
  const manual = edits.filter((e) => e.action === "add_invariant");
  const byItem = useMemo(() => {
    const m = new Map<string, KnowledgeEdit[]>();
    for (const e of edits) if (e.action !== "add_invariant") m.set(e.item_id, [...(m.get(e.item_id) ?? []), e]);
    return m;
  }, [edits]);
  const sha = version.sources.default_branch_sha;
  if (items.length === 0 && manual.length === 0) return <EmptyState icon="book" title="Bản này không có bất biến" />;
  return (
    <>
      <div className="table-wrap">
        <table className="stack-sm knowledge-table">
          <thead>
            <tr>
              <th>Mã</th>
              <th>Phát biểu</th>
              <th>Severity</th>
              <th>Tín hiệu</th>
              <th>Nguồn</th>
              <th className="num">Độ tin cậy</th>
              {canEdit && <th>Thao tác</th>}
            </tr>
          </thead>
          <tbody>
            {items.map((it) => (
              <InvariantRow
                key={`${version.id}-${it.id}`}
                item={it}
                edits={byItem.get(it.id) ?? []}
                canEdit={canEdit}
                webUrl={webUrl}
                sha={sha}
                onEdit={(action, payload) => onEdit(it.id, action, payload)}
                onRemove={onRemove}
              />
            ))}
            {manual.map((e) => (
              <tr key={`manual-${e.id}`}>
                <td data-label="Mã">
                  <code>{e.item_id}</code>
                  <Pill tone="primary">tay</Pill>
                </td>
                <td data-label="Phát biểu">
                  {e.payload.statement}
                  <div className="small muted">{who(e)}</div>
                </td>
                <td data-label="Severity">{e.payload.severity ? <SeverityBadge severity={e.payload.severity} /> : "—"}</td>
                <td data-label="Tín hiệu">
                  <Chips items={e.payload.signals} />
                </td>
                <td data-label="Nguồn" className="muted">
                  thêm trên CodePulse
                </td>
                <td className="num" data-label="Độ tin cậy">
                  —
                </td>
                {canEdit && (
                  <td data-label="Thao tác">
                    <button type="button" className="sm" onClick={() => void onRemove(e)}>
                      Xoá
                    </button>
                  </td>
                )}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {canEdit && <AddInvariantForm count={manual.length} onAdd={(payload) => onEdit(`manual-${Date.now()}`, "add_invariant", payload)} />}
    </>
  );
}

/* ---------- Other tabs ---------- */

function ChecksTab({ version, webUrl }: { version: KnowledgeVersion; webUrl: string | null | undefined }) {
  const checks = version.data.checks ?? [];
  if (checks.length === 0) return <EmptyState icon="book" title="Bản này không có check" />;
  return (
    <div className="stack" style={{ gap: 8 }}>
      {checks.map((c) => (
        <details key={c.id} className="knowledge-check">
          <summary>
            <strong>{c.name}</strong> <code className="muted">{c.id}</code>
            <span className="muted small">
              {" "}
              · {c.items.length} mục · độ tin cậy {pct(c.confidence)}
            </span>
          </summary>
          <dl className="facts">
            <dt>Kích hoạt khi</dt>
            <dd>
              {c.triggers.globs?.length ? (
                <div>
                  đường dẫn: <Chips items={c.triggers.globs} />
                </div>
              ) : null}
              {c.triggers.signals?.length ? (
                <div>
                  tín hiệu: <Chips items={c.triggers.signals} />
                </div>
              ) : null}
              {c.triggers.keywords?.length ? (
                <div>
                  từ khoá trong diff: <Chips items={c.triggers.keywords} />
                </div>
              ) : null}
              {!c.triggers.globs?.length && !c.triggers.signals?.length && !c.triggers.keywords?.length && <span className="faint">—</span>}
            </dd>
            <dt>Nguồn</dt>
            <dd>
              <SourceRef source={c.source} webUrl={webUrl} sha={version.sources.default_branch_sha} />
            </dd>
          </dl>
          <ol style={{ margin: "8px 0 0", paddingLeft: 20 }}>
            {c.items.map((it, i) => (
              <li key={i}>{it}</li>
            ))}
          </ol>
        </details>
      ))}
    </div>
  );
}

function DomainTab({ version, webUrl }: { version: KnowledgeVersion; webUrl: string | null | undefined }) {
  const d = version.data.domain;
  const p = version.data.procedure;
  if (!d && !p) return <EmptyState icon="book" title="Bản này chưa có phần nghiệp vụ" />;
  return (
    <div className="stack" style={{ gap: 16 }}>
      {d && (
        <dl className="facts knowledge-facts">
          <dt>Tóm tắt</dt>
          <dd>{d.summary || "—"}</dd>
          <dt>Tác nhân</dt>
          <dd>
            <Chips items={d.actors} />
          </dd>
          <dt>Tenant</dt>
          <dd>{d.tenancy || "—"}</dd>
          <dt>Dòng tiền</dt>
          <dd>
            {d.money_flows?.length ? (
              <ul style={{ margin: 0, paddingLeft: 18 }}>
                {d.money_flows.map((m) => (
                  <li key={m}>{m}</li>
                ))}
              </ul>
            ) : (
              "—"
            )}
          </dd>
          <dt>Module trọng yếu</dt>
          <dd>
            {d.critical_modules?.length ? (
              <ul style={{ margin: 0, paddingLeft: 18 }}>
                {d.critical_modules.map((m) => (
                  <li key={m.path}>
                    <code>{m.path}</code> — {m.why}
                  </li>
                ))}
              </ul>
            ) : (
              "—"
            )}
          </dd>
          <dt>Thuật ngữ</dt>
          <dd>
            {d.glossary?.length ? (
              <ul style={{ margin: 0, paddingLeft: 18 }}>
                {d.glossary.map((g) => (
                  <li key={g.term}>
                    <strong>{g.term}</strong>: {g.meaning}
                  </li>
                ))}
              </ul>
            ) : (
              "—"
            )}
          </dd>
          <dt>Stack</dt>
          <dd>
            <Chips items={d.stack} />
          </dd>
        </dl>
      )}
      {p && (
        <section>
          <h4 style={{ margin: "0 0 6px" }}>
            Quy trình review của dự án <span className="muted small">(từ lệnh review trong .claude)</span>
          </h4>
          <ol style={{ margin: 0, paddingLeft: 20 }}>
            {p.steps.map((s, i) => (
              <li key={i}>{s}</li>
            ))}
          </ol>
          {Object.keys(p.severity_map ?? {}).length > 0 && (
            <dl className="facts" style={{ marginTop: 8 }}>
              {Object.entries(p.severity_map).map(([k, v]) => (
                <div key={k} style={{ display: "contents" }}>
                  <dt>{k}</dt>
                  <dd>{v}</dd>
                </div>
              ))}
            </dl>
          )}
          <div className="small muted" style={{ marginTop: 6 }}>
            Nguồn: <SourceRef source={p.source} webUrl={webUrl} sha={version.sources.default_branch_sha} />
          </div>
        </section>
      )}
    </div>
  );
}

function SeverityRulesTab({ version, webUrl }: { version: KnowledgeVersion; webUrl: string | null | undefined }) {
  const rules = version.data.severity_rules ?? [];
  if (rules.length === 0) return <EmptyState icon="book" title="Bản này không có quy tắc severity" />;
  return (
    <div className="table-wrap">
      <table className="stack-sm">
        <thead>
          <tr>
            <th>Mã</th>
            <th>Khi</th>
            <th>Hiệu lực</th>
            <th>Lý do</th>
            <th>Nguồn</th>
          </tr>
        </thead>
        <tbody>
          {rules.map((r) => (
            <tr key={r.id}>
              <td data-label="Mã">
                <code>{r.id}</code>
              </td>
              <td data-label="Khi">
                <Chips items={[...(r.when.globs ?? []), ...(r.when.signals ?? [])]} />
              </td>
              <td data-label="Hiệu lực">
                {r.effect.raise_to && (
                  <>
                    nâng lên <SeverityBadge severity={r.effect.raise_to} />{" "}
                  </>
                )}
                {r.effect.lower_to && (
                  <>
                    hạ xuống <SeverityBadge severity={r.effect.lower_to} />{" "}
                  </>
                )}
                {typeof r.effect.min_confidence === "number" && <span className="muted small">tin cậy ≥ {pct(r.effect.min_confidence)}</span>}
              </td>
              <td data-label="Lý do">{r.reason}</td>
              <td data-label="Nguồn">
                <SourceRef source={r.source} webUrl={webUrl} sha={version.sources.default_branch_sha} />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function ConventionsTab({ version }: { version: KnowledgeVersion }) {
  const rows = version.data.code_conventions ?? [];
  if (rows.length === 0) return <EmptyState icon="book" title="Không thấy convention nào ngoài tài liệu" />;
  return (
    <div className="table-wrap">
      <table className="stack-sm">
        <thead>
          <tr>
            <th>Convention</th>
            <th>Bằng chứng</th>
            <th className="num">Độ tin cậy</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((c, i) => (
            <tr key={i}>
              <td data-label="Convention">{c.statement}</td>
              <td data-label="Bằng chứng">
                <code>{c.evidence}</code>
              </td>
              <td className="num" data-label="Độ tin cậy">
                {pct(c.confidence)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function IndexTab({ version }: { version: KnowledgeVersion }) {
  const rows = version.data.index ?? [];
  if (rows.length === 0) return <EmptyState icon="book" title="Mục lục trống" />;
  return (
    <div className="table-wrap">
      <table className="stack-sm">
        <thead>
          <tr>
            <th>File</th>
            <th>Nguồn</th>
            <th>Loại</th>
            <th>Mục đích</th>
            <th>Rút ra</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={`${r.source}:${r.path}`}>
              <td data-label="File">
                <code>{r.path}</code>
              </td>
              <td data-label="Nguồn" className="muted">
                {INDEX_SOURCE[r.source] ?? r.source}
              </td>
              <td data-label="Loại" className="muted">
                {r.kind}
              </td>
              <td data-label="Mục đích">{r.purpose}</td>
              <td data-label="Rút ra">
                <Chips items={r.used_for} empty="không mục nào" />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function OmittedTab({ version }: { version: KnowledgeVersion }) {
  const rows = version.data.omitted ?? [];
  if (rows.length === 0) return <EmptyState icon="checkCircle" tone="good" title="Không bỏ file nào" />;
  return (
    <div className="table-wrap">
      <table className="stack-sm">
        <thead>
          <tr>
            <th>File / đoạn</th>
            <th>Lý do bỏ</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={`${r.path}-${i}`}>
              <td data-label="File">
                <code>{r.path}</code>
              </td>
              <td data-label="Lý do" className="muted">
                {OMITTED_REASON[r.reason] ?? r.reason}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function ConflictsTab({ version }: { version: KnowledgeVersion }) {
  const rows = version.data.conflicts ?? [];
  if (rows.length === 0) return <EmptyState icon="checkCircle" tone="good" title="Các nguồn không mâu thuẫn" />;
  return (
    <div className="table-wrap">
      <table className="stack-sm">
        <thead>
          <tr>
            <th>Nguồn A</th>
            <th>Nguồn B</th>
            <th>Đã chọn</th>
            <th>Vì sao</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={i}>
              <td data-label="A">{r.a}</td>
              <td data-label="B">{r.b}</td>
              <td data-label="Đã chọn">
                <strong>{r.chosen}</strong>
              </td>
              <td data-label="Vì sao" className="muted">
                {r.why}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

type DiffRow = { kind: string; id: string; before?: string; after?: string };

/** Added / removed / reworded items by id between two versions (docs/specs/19 §3.5). */
function diffVersions(current: Knowledge, previous: Knowledge): { added: DiffRow[]; removed: DiffRow[]; reworded: DiffRow[] } {
  const added: DiffRow[] = [];
  const removed: DiffRow[] = [];
  const reworded: DiffRow[] = [];
  const groups: { kind: string; cur: { id: string; text: string }[]; prev: { id: string; text: string }[] }[] = [
    {
      kind: "Bất biến",
      cur: (current.invariants ?? []).map((i) => ({ id: i.id, text: i.statement })),
      prev: (previous.invariants ?? []).map((i) => ({ id: i.id, text: i.statement })),
    },
    {
      kind: "Check",
      cur: (current.checks ?? []).map((c) => ({ id: c.id, text: `${c.name}: ${c.items.join(" | ")}` })),
      prev: (previous.checks ?? []).map((c) => ({ id: c.id, text: `${c.name}: ${c.items.join(" | ")}` })),
    },
    {
      kind: "Quy tắc severity",
      cur: (current.severity_rules ?? []).map((r) => ({ id: r.id, text: r.reason })),
      prev: (previous.severity_rules ?? []).map((r) => ({ id: r.id, text: r.reason })),
    },
  ];
  for (const g of groups) {
    const before = new Map(g.prev.map((p) => [p.id, p.text]));
    const after = new Map(g.cur.map((c) => [c.id, c.text]));
    for (const c of g.cur) {
      if (!before.has(c.id)) added.push({ kind: g.kind, id: c.id, after: c.text });
      else if (before.get(c.id) !== c.text) reworded.push({ kind: g.kind, id: c.id, before: before.get(c.id), after: c.text });
    }
    for (const p of g.prev) if (!after.has(p.id)) removed.push({ kind: g.kind, id: p.id, before: p.text });
  }
  return { added, removed, reworded };
}

function DiffTab({ projectId, version, previousRow }: { projectId: string; version: KnowledgeVersion; previousRow: KnowledgeVersionRow | null }) {
  const previous = useApi<KnowledgeVersion>(previousRow ? `${knowledgePath(projectId)}/versions/${previousRow.version}` : null);
  if (!previousRow) return <EmptyState icon="book" title="Đây là bản đầu tiên, chưa có bản trước để so" />;
  if (!previous.data) return <Loading error={previous.error} rows={3} />;
  const { added, removed, reworded } = diffVersions(version.data, previous.data.data);
  if (!added.length && !removed.length && !reworded.length)
    return <EmptyState icon="checkCircle" tone="good" title={`Không khác bản v${previousRow.version} về bất biến, check và quy tắc severity`} />;
  const section = (title: string, tone: "good" | "bad" | "info", rows: DiffRow[]) =>
    rows.length > 0 && (
      <section>
        <h4 style={{ margin: "0 0 6px" }}>
          <Pill tone={tone}>{title}</Pill> <span className="muted small">{rows.length} mục</span>
        </h4>
        <ul style={{ margin: 0, paddingLeft: 18 }}>
          {rows.map((r) => (
            <li key={`${r.kind}:${r.id}`}>
              <span className="muted small">{r.kind}</span> <code>{r.id}</code>
              {r.before !== undefined && r.after !== undefined ? (
                <div className="small">
                  <del className="muted">{r.before}</del>
                  <br />
                  <ins style={{ textDecoration: "none" }}>{r.after}</ins>
                </div>
              ) : (
                <div className="small muted">{r.after ?? r.before}</div>
              )}
            </li>
          ))}
        </ul>
      </section>
    );
  return (
    <div className="stack" style={{ gap: 14 }}>
      <p className="muted small" style={{ margin: 0 }}>
        So bản v{version.version} với bản v{previousRow.version} (dựng {dateTime(previousRow.created_at)}) theo <code>id</code> của từng mục.
      </p>
      {section("Thêm", "good", added)}
      {section("Bớt", "bad", removed)}
      {section("Đổi lời", "info", reworded)}
    </div>
  );
}

const RULE_LABEL: Record<NonNullable<KnowledgeRuleStat["label"]>, { text: string; tone: "bad" | "warn"; hint: string }> = {
  dead: { text: "Chết", tone: "warn", hint: "Agent nói rule áp dụng nhưng chưa bao giờ nổ trong cửa sổ (đáng tin ở 90 ngày)" },
  noisy: { text: "Nhiễu", tone: "bad", hint: "Rejected + dispute được chấp nhận ≥ 50% số lần nổ" },
};

function RuleLabel({ label }: { label: KnowledgeRuleStat["label"] }) {
  if (!label) return <span className="muted">—</span>;
  const l = RULE_LABEL[label];
  return (
    <span title={l.hint}>
      <Pill tone={l.tone}>{l.text}</Pill>
    </span>
  );
}

function RuleStatsTable({ projectId, days }: { projectId: string; days: 30 | 90 }) {
  const { data, error } = useApi<KnowledgeStats>(knowledgeStatsPath(projectId, days), { keepPreviousData: true });
  if (!data) return <Loading error={error} rows={3} />;
  if (data.rules.length === 0)
    return (
      <EmptyState icon="inbox" title="Chưa có số liệu">
        {data.reviews === 0
          ? `Chưa có review xong trong ${days} ngày qua.`
          : `${data.reviews} review trong ${days} ngày qua nhưng không finding nào viện dẫn rule của tiền đề và agent không khai rule nào áp dụng.`}
      </EmptyState>
    );
  return (
    <div className="stack" style={{ gap: 6 }}>
      <p className="muted small" style={{ margin: 0 }}>
        {num(data.reviews)} review xong từ {dateTime(data.since)}. Nổ = finding viện dẫn rule; áp dụng không nổ = review mà agent nói rule áp dụng nhưng
        không báo gì.
      </p>
      <div className="table-wrap">
        <table className="table">
          <thead>
            <tr>
              <th>Rule</th>
              <th className="num">Nổ</th>
              <th className="num">Confirmed</th>
              <th className="num">Downgraded</th>
              <th className="num">Rejected</th>
              <th className="num">Dispute</th>
              <th className="num">Áp dụng không nổ</th>
              <th>Nhãn</th>
            </tr>
          </thead>
          <tbody>
            {data.rules.map((r) => (
              <tr key={r.rule_id}>
                <td>
                  <code>{r.rule_id}</code>
                </td>
                <td className="num">{num(r.fired)}</td>
                <td className="num">{num(r.confirmed)}</td>
                <td className="num">{num(r.downgraded)}</td>
                <td className="num">{num(r.rejected)}</td>
                <td className="num">{num(r.disputes_accepted)}</td>
                <td className="num">{num(r.applied_not_fired)}</td>
                <td>
                  <RuleLabel label={r.label} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function StatsTab({ projectId, version }: { projectId: string; version: KnowledgeVersion }) {
  const m = version.data.meta;
  const [days, setDays] = useState<30 | 90>(30);
  return (
    <div className="stack" style={{ gap: 14 }}>
      {m && (
        <dl className="facts knowledge-facts">
          <dt>Dựng lúc</dt>
          <dd>{dateTime(m.built_at)}</dd>
          <dt>Model</dt>
          <dd>{m.model || "—"}</dd>
          <dt>Chi phí</dt>
          <dd>{typeof m.cost_usd === "number" ? `$${num(m.cost_usd, 3)}` : "—"}</dd>
          <dt>Lượt / file đọc</dt>
          <dd>
            {m.turns ?? "—"} lượt · {m.files_read ?? "—"} file
          </dd>
          <dt>Độ tin cậy chung</dt>
          <dd>{pct(m.confidence)}</dd>
          <dt>Nguồn (sha)</dt>
          <dd>
            {Object.entries(version.sources)
              .filter(([, v]) => v)
              .map(([k, v]) => (
                <span key={k} style={{ marginRight: 10 }}>
                  {k}: <code>{String(v).slice(0, 10)}</code>
                </span>
              ))}
            {!Object.values(version.sources).some(Boolean) && "—"}
          </dd>
        </dl>
      )}
      <div className="row" style={{ alignItems: "center", gap: 8 }}>
        <h4 style={{ margin: 0 }}>Thống kê theo rule</h4>
        <div className="tabs" role="tablist" aria-label="Cửa sổ thống kê">
          {([30, 90] as const).map((d) => (
            <button key={d} type="button" role="tab" aria-selected={days === d} className={days === d ? "active" : undefined} onClick={() => setDays(d)}>
              {d} ngày
            </button>
          ))}
        </div>
      </div>
      <RuleStatsTable projectId={projectId} days={days} />
    </div>
  );
}

/* ---------- Page ---------- */

export default function KnowledgePage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const { data, error, mutate } = useApi<KnowledgeResponse>(knowledgePath(id), { refreshInterval: knowledgePoll });
  const versions = useApi<KnowledgeVersionRow[]>(`${knowledgePath(id)}/versions`);
  const repos = useApi<Repo[]>("/repos");
  const { data: me } = useApi<Me>("/auth/me");
  const [selected, setSelected] = useState<number | null>(null);
  const [tab, setTab] = useState<Tab>("invariants");
  const [message, setMessage] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const repo = repos.data?.find((r) => r.id === Number(id));
  const activeVersion = data?.active?.version ?? null;
  const viewing = selected ?? activeVersion;
  const other = useApi<KnowledgeVersion>(viewing !== null && viewing !== activeVersion ? `${knowledgePath(id)}/versions/${viewing}` : null);
  const shown: KnowledgeVersion | undefined = viewing === activeVersion ? (data?.active ?? undefined) : other.data;
  const canEdit = canEditKnowledge(me);
  const edits = data?.edits ?? [];
  const previousRow = useMemo(() => {
    if (!shown || !versions.data) return null;
    return versions.data.filter((v) => v.version < shown.version).sort((a, b) => b.version - a.version)[0] ?? null;
  }, [shown, versions.data]);
  const orphans = useMemo(() => {
    if (!shown) return [];
    const ids = new Set([
      ...(shown.data.invariants ?? []).map((i) => i.id),
      ...(shown.data.checks ?? []).map((c) => c.id),
      ...(shown.data.severity_rules ?? []).map((r) => r.id),
    ]);
    return edits.filter((e) => e.action !== "add_invariant" && !ids.has(e.item_id));
  }, [shown, edits]);

  async function run(work: () => Promise<unknown>, done: string): Promise<boolean> {
    setBusy(true);
    try {
      await work();
      await mutate();
      setMessage(done);
      return true;
    } catch (e: unknown) {
      setMessage(`Lỗi: ${e instanceof ApiError ? e.message : String(e)}`);
      return false;
    } finally {
      setBusy(false);
    }
  }
  const approve = () => run(() => knowledgeApi.approve(id), "Đã duyệt bản tiền đề này; review và comment MR thôi ghi “chưa được lead duyệt”.");
  const addEdit = (item_id: string, action: KnowledgeEdit["action"], payload: KnowledgeEdit["payload"]) =>
    run(
      () => knowledgeApi.addEdit(id, { item_id, action, payload }),
      action === "disable"
        ? "Đã tắt mục này; áp cho mọi bản sau khi mục còn tồn tại."
        : action === "add_invariant"
          ? "Đã thêm bất biến tay."
          : "Đã lưu sửa tay; áp cho mọi bản sau khi mục còn tồn tại.",
    );
  const removeEdit = (edit: KnowledgeEdit) => run(() => knowledgeApi.removeEdit(id, edit.id), "Đã bỏ sửa tay.");

  const header = (
    <PageHeader
      eyebrow={
        <Link href={repo?.business_project_id ? `/projects/${repo.business_project_id}` : "/overview"} className="muted">
          ← {repo?.business_project ?? "Dự án"}
        </Link>
      }
      title={
        <>
          Tiền đề review <span className="muted">· {repo?.path_with_namespace ?? `repo #${id}`}</span>
        </>
      }
      subtitle={
        shown ? (
          <>
            Bản v{shown.version} · dựng {dateTime(shown.created_at)} · {BUILT_BY_LABEL[shown.built_by] ?? shown.built_by}
            {shown.status !== "active" ? " · bản cũ, chỉ xem" : ""}
          </>
        ) : (
          "Nghiệp vụ, bất biến, check và quy tắc severity mà reviewer dùng cho repo này"
        )
      }
      actions={
        data && (
          <>
            {versions.data && versions.data.length > 0 && (
              <select
                className="compact"
                aria-label="Phiên bản"
                value={viewing ?? ""}
                onChange={(e) => setSelected(Number(e.target.value) === activeVersion ? null : Number(e.target.value))}
              >
                {versions.data.map((v) => (
                  <option key={v.id} value={v.version}>
                    v{v.version} · {dateTime(v.created_at)}
                    {v.version === activeVersion ? " · đang dùng" : ""}
                    {v.approved_at ? " · đã duyệt" : ""}
                  </option>
                ))}
              </select>
            )}
            {canEdit && shown && shown.status === "active" && !shown.approved_by && (
              <button type="button" className="primary" disabled={busy} onClick={() => void approve()}>
                Duyệt bản này
              </button>
            )}
            {repo && (
              <RefreshKnowledgeButton projectId={repo.id} repoName={repo.path_with_namespace} building={data.building} me={me} onQueued={mutate} />
            )}
          </>
        )
      }
    />
  );

  if (!data)
    return (
      <>
        {header}
        <Loading error={error} rows={4} />
      </>
    );

  return (
    <>
      {header}
      <Message text={message} onClose={() => setMessage(null)} />
      {data.building && (
        <Alert tone="info">
          Đang rà soát lại repo; trang này tự cập nhật khi worker lưu bản mới. Tiến trình chi tiết ở panel Đang review.
        </Alert>
      )}
      {!shown ? (
        viewing === null ? (
          <Card>
            <EmptyState icon="book" title="Chưa có tiền đề review">
              Lần review sâu đầu tiên của repo sẽ tự dựng tiền đề; lead có thể dựng ngay bằng nút Rà soát lại dự án.
            </EmptyState>
          </Card>
        ) : (
          <Loading error={other.error} rows={4} />
        )
      ) : (
        <>
          <div className="chips" style={{ marginBottom: 12 }}>
            {shown.stale && <StalePill reasons={shown.stale_reasons} />}
            <ApprovalPill active={shown} />
            {shown.status !== "active" && <Pill tone="info">bản cũ (đang dùng v{activeVersion})</Pill>}
            <span className="muted small">
              {shown.counts.invariants} bất biến · {shown.counts.checks} check · {shown.counts.severity_rules} quy tắc severity ·{" "}
              {shown.counts.code_conventions} convention · {shown.counts.omitted} đã bỏ · {shown.counts.conflicts} mâu thuẫn
            </span>
          </div>
          {shown.stale && (
            <Alert tone="warning">
              <code>.claude</code> ở default branch hoặc repo dùng chung đã đổi sau khi dựng bản này. Review vẫn dùng bản này; bấm
              Rà soát lại dự án để biên dịch bản mới.
            </Alert>
          )}
          {orphans.length > 0 && (
            <Alert tone="warning">
              {orphans.length} sửa tay mồ côi: mục không còn trong bản này (
              {orphans.map((o) => (
                <code key={o.id} style={{ marginRight: 4 }}>
                  {o.item_id}
                </code>
              ))}
              ).{" "}
              {canEdit && (
                <button
                  type="button"
                  className="link sm"
                  disabled={busy}
                  onClick={() => {
                    void (async () => {
                      for (const o of orphans) if (!(await removeEdit(o))) break;
                    })();
                  }}
                >
                  Bỏ các sửa tay này
                </button>
              )}
            </Alert>
          )}
          <Card>
            <div className="tabs" role="tablist" aria-label="Nội dung tiền đề">
              {TABS.map((t) => (
                <button key={t.id} type="button" role="tab" aria-selected={tab === t.id} className={tab === t.id ? "active" : undefined} onClick={() => setTab(t.id)}>
                  {t.label}
                  {t.count ? <span className="muted small"> {t.count(shown.data)}</span> : null}
                </button>
              ))}
            </div>
            {tab === "invariants" && (
              <InvariantsTab version={shown} edits={edits} canEdit={canEdit} webUrl={repo?.web_url} onEdit={addEdit} onRemove={removeEdit} />
            )}
            {tab === "checks" && <ChecksTab version={shown} webUrl={repo?.web_url} />}
            {tab === "domain" && <DomainTab version={shown} webUrl={repo?.web_url} />}
            {tab === "severity" && <SeverityRulesTab version={shown} webUrl={repo?.web_url} />}
            {tab === "conventions" && <ConventionsTab version={shown} />}
            {tab === "index" && <IndexTab version={shown} />}
            {tab === "omitted" && <OmittedTab version={shown} />}
            {tab === "conflicts" && <ConflictsTab version={shown} />}
            {tab === "diff" && <DiffTab projectId={id} version={shown} previousRow={previousRow} />}
            {tab === "stats" && <StatsTab projectId={id} version={shown} />}
          </Card>
        </>
      )}
    </>
  );
}
