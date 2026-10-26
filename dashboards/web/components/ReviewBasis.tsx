import { Card, Pill } from "@/components/ui";
import { date } from "@/lib/format";
import type { BasisRule, Finding, ReviewBasis, ReviewRound } from "@/lib/types";

const RULE_KIND: Record<string, string> = {
  business_project: "Dự án",
  project: "Repo",
  file: "File trong repo",
};
const RULE_STATUS: Record<string, { label: string; tone?: "good" | "warn" | "bad" }> = {
  used: { label: "Đã đối chiếu", tone: "good" },
  missing: { label: "Không tìm thấy file", tone: "warn" },
  changed_in_diff: { label: "Bỏ qua: bị sửa trong chính diff", tone: "warn" },
  partial: { label: "Chỉ đưa vào một phần (quá dài)", tone: "warn" },
  cut: { label: "Vượt giới hạn, không đưa vào", tone: "bad" },
};
const INSTRUCTION_KIND: { kind: string; label: string }[] = [
  { kind: "skill", label: "Kỹ năng" },
  { kind: "agent", label: "Reviewer agent" },
  { kind: "rules", label: "Quy tắc" },
  { kind: "command", label: "Lệnh review" },
  { kind: "instructions", label: "Hướng dẫn AI" },
  { kind: "doc", label: "Tài liệu" },
];
const NOT_LOADED_REASON: Record<string, string> = {
  catalog: "trong mục lục, agent mở khi cần",
  // Reviews before the per-commit selection.
  name: "tên không thuộc nhóm review",
  budget: "hết giới hạn ký tự",
  duplicate: "trùng nội dung, chỉ đưa vào một bản",
};

/** "v2 · 2 quy tắc · 5 tài liệu" for the history table. */
export function basisSummary(basis: ReviewBasis | null): string {
  if (!basis) return "—";
  const rules = (basis.rules ?? []).filter((r) => r.status === "used").length;
  const docs = (basis.instructions ?? []).filter((d) => d.status !== "cut");
  const shared = docs.filter((d) => d.source === "shared").length;
  return [
    basis.rubric ? `rubric ${basis.rubric.version}` : null,
    `${rules} quy tắc`,
    docs.length - shared ? `${docs.length - shared} tài liệu repo` : null,
    shared ? `${shared} file .claude dùng chung` : null,
  ]
    .filter(Boolean)
    .join(" · ");
}

/** The nearest earlier review that actually ran (done or superseded) and recorded a basis. */
export function previousRun(history: ReviewRound[], index: number): ReviewRound | null {
  for (let i = index - 1; i >= 0; i--) {
    const h = history[i];
    if ((h.status === "done" || h.status === "superseded") && h.basis) return h;
  }
  return null;
}

/** Rules whose text changed since an earlier review (same name, different hash). */
export function changedRules(basis: ReviewBasis | null, previous: ReviewBasis | null): string[] {
  if (!basis?.rules || !previous?.rules) return [];
  const before = new Map(previous.rules.map((r) => [r.name, r.hash]));
  return basis.rules
    .filter((r) => r.hash && before.has(r.name) && before.get(r.name) !== r.hash)
    .map((r) => r.name);
}

/**
 * What this review was checked against (docs/specs/17 §Căn cứ review): the rubric, the team
 * rules with how many findings cite each, and the repository instructions, reviewer agents and
 * skills the deep pass read. Names and fingerprints only; the rule text stays in Admin / the repo.
 */
export function BasisCard({
  basis,
  findings,
  mode,
  deepError,
  history,
  reviewId,
}: {
  basis: ReviewBasis | null;
  findings: Finding[];
  mode: "fast" | "deep";
  /** The deep pass was tried and failed (the review fell back to the first pass). */
  deepError?: boolean;
  history: ReviewRound[];
  reviewId: number;
}) {
  if (!basis) {
    return (
      <Card title="Căn cứ review">
        <p className="muted" style={{ margin: 0 }}>
          Lần review này chạy trước khi CodePulse ghi lại căn cứ đối chiếu.
        </p>
      </Card>
    );
  }
  const previous = previousRun(history, history.findIndex((h) => h.id === reviewId));
  const changed = new Set(changedRules(basis, previous?.basis ?? null));
  const cites = (rule: BasisRule) => findings.filter((f) => f.rule_id === rule.name).length;
  const rules = basis.rules ?? [];
  const procedure = (basis.instructions ?? []).filter((d) => d.procedure);
  const instructions = (basis.instructions ?? []).filter((d) => !d.procedure);
  const notLoaded = basis.instructions_not_loaded ?? [];
  const opened = notLoaded.filter((d) => d.read);
  const indexed = notLoaded.filter((d) => !d.read);
  const sharedRepo = basis.claude_repo;

  return (
    <Card title="Căn cứ review" subtitle="Những gì AI đã đối chiếu trong lần review này">
      <div className="basis">
        {basis.rubric && (
          <section>
            <h4>Rubric {basis.rubric.version}</h4>
            <div className="chips" style={{ margin: 0 }}>
              {basis.rubric.lens.map((l, i) => (
                <span key={l} className="chip">
                  {i + 1}. {l}
                </span>
              ))}
            </div>
          </section>
        )}

        <section>
          <h4>
            Quy tắc đội
            {basis.rules_ref ? <span className="muted small"> · file đọc ở nhánh {basis.rules_ref}</span> : null}
          </h4>
          {rules.length === 0 ? (
            <p className="muted small" style={{ margin: 0 }}>
              Chưa cấu hình quy tắc cho dự án / repo này (Admin › Repo › Quy tắc review).
            </p>
          ) : (
            <div className="table-wrap">
              <table className="stack-sm">
                <thead>
                  <tr>
                    <th>Quy tắc</th>
                    <th>Nguồn</th>
                    <th>Trạng thái</th>
                    <th className="num">Finding viện dẫn</th>
                    <th>Dấu vân tay</th>
                  </tr>
                </thead>
                <tbody>
                  {rules.map((r) => {
                    const st = RULE_STATUS[r.status] ?? { label: r.status };
                    const n = cites(r);
                    return (
                      <tr key={`${r.kind}:${r.name}`}>
                        <td data-label="Quy tắc">
                          {r.kind === "file" ? <code>{r.name}</code> : r.name}
                          {changed.has(r.name) && (
                            <Pill tone="info">đã đổi so với #{previous?.id}</Pill>
                          )}
                        </td>
                        <td className="muted" data-label="Nguồn">
                          {RULE_KIND[r.kind] ?? r.kind}
                        </td>
                        <td data-label="Trạng thái">
                          <Pill tone={st.tone}>{st.label}</Pill>
                        </td>
                        <td className="num" data-label="Finding viện dẫn">
                          {n || "—"}
                        </td>
                        <td className="muted" data-label="Dấu vân tay">
                          {r.hash ? <code title={r.chars ? `${r.chars} ký tự` : undefined}>{r.hash}</code> : "—"}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </section>

        <section>
          <h4>Tài liệu, agent và kỹ năng (.claude)</h4>
          {mode === "deep" && sharedRepo && (
            <p className="small" style={{ margin: "0 0 6px" }}>
              Repo .claude dùng chung của dự án: <code>{sharedRepo.repo}</code>
              {sharedRepo.status === "used" ? (
                <span className="muted">
                  {" "}
                  @ {sharedRepo.ref} ({sharedRepo.sha})
                  {sharedRepo.mount_error ? (
                    <Pill tone="warn">không chép được vào bản sao: agent chỉ dùng phần được trích</Pill>
                  ) : null}
                </span>
              ) : (
                <Pill tone="warn">
                  {sharedRepo.status === "other_connection" ? "khác kết nối GitLab, không đọc được" : "không tải được"}
                </Pill>
              )}
            </p>
          )}
          {mode !== "deep" ? (
            <p className="muted small" style={{ margin: 0 }}>
              {deepError
                ? "Review sâu đã thử mở repo nhưng lỗi (xem thẻ Đối chứng trong mã nguồn); kết quả dựa trên diff và quy tắc ở trên."
                : "Review nhanh: chỉ đọc diff và quy tắc ở trên, không mở mã nguồn của repo."}
            </p>
          ) : instructions.length === 0 && procedure.length === 0 ? (
            <p className="muted small" style={{ margin: 0 }}>
              Review sâu đã mở repo nhưng không thấy CLAUDE.md, AGENTS.md, .claude/rules, agent hay kỹ năng review nào.
            </p>
          ) : (
            <dl className="basis-docs">
              {procedure.length > 0 && (
                <div>
                  <dt>Quy trình review</dt>
                  <dd>
                    {procedure.map((d) => (
                      <DocRef key={`${d.source}:${d.path}`} doc={d} sharedRepo={sharedRepo?.repo} />
                    ))}
                  </dd>
                </div>
              )}
              {INSTRUCTION_KIND.map(({ kind, label }) => {
                const paths = instructions.filter((d) => d.kind === kind);
                if (!paths.length) return null;
                return (
                  <div key={kind}>
                    <dt>{label}</dt>
                    <dd>
                      {paths.map((d) => (
                        <DocRef key={`${d.source}:${d.path}`} doc={d} sharedRepo={sharedRepo?.repo} />
                      ))}
                    </dd>
                  </div>
                );
              })}
              {opened.length > 0 && (
                <div>
                  <dt>Agent tự mở thêm</dt>
                  <dd>
                    {opened.map((d) => (
                      <DocRef key={`${d.source}:${d.path}`} doc={d} sharedRepo={sharedRepo?.repo} />
                    ))}
                  </dd>
                </div>
              )}
            </dl>
          )}
          {mode === "deep" && indexed.length > 0 && (
            <details className="small" style={{ marginTop: 6 }}>
              <summary className="muted">
                {indexed.some((d) => d.reason === "catalog")
                  ? `${indexed.length} file .claude khác nằm trong mục lục, agent không cần mở cho thay đổi này`
                  : `Có ${indexed.length} file .claude không được nạp`}
              </summary>
              <ul className="muted" style={{ margin: "4px 0 0", paddingLeft: 18 }}>
                {indexed.map((d) => (
                  <li key={`${d.source}:${d.path}`}>
                    <code>{d.path}</code>
                    {d.source === "shared" ? " (dùng chung)" : ""} · {NOT_LOADED_REASON[d.reason] ?? d.reason}
                    {d.same_as ? (
                      <>
                        {" "}
                        (giống <code>{d.same_as}</code>)
                      </>
                    ) : null}
                    {d.why?.length ? ` · ${d.why.join("; ")}` : ""}
                  </li>
                ))}
              </ul>
              <p className="muted" style={{ margin: "4px 0 0" }}>
                Muốn luôn trích một file: Admin › Repo › mở dự án › Repo .claude dùng chung › File nạp thêm.
              </p>
            </details>
          )}
          {basis.profile && (
            <p className="muted small" style={{ marginBottom: 0 }}>
              Hồ sơ dự án dựng từ commit <code>{basis.profile.sha ?? "?"}</code>
              {basis.profile.built_at ? ` ngày ${date(basis.profile.built_at)}` : ""}
              {typeof basis.profile.confidence === "number"
                ? `, độ tin cậy ${Math.round(basis.profile.confidence * 100)}%`
                : ""}
              .
            </p>
          )}
        </section>
      </div>
    </Card>
  );
}

type BasisDoc = Pick<
  NonNullable<ReviewBasis["instructions"]>[number],
  "path" | "source" | "status" | "why" | "read"
>;

/** One quoted or opened Claude file: its path, where it came from and why it was chosen. */
function DocRef({ doc, sharedRepo }: { doc: BasisDoc; sharedRepo?: string }) {
  return (
    <span>
      <code title={doc.source === "shared" ? `từ ${sharedRepo ?? "repo dùng chung"}` : undefined}>{doc.path}</code>
      {doc.source === "shared" && <Pill tone="info">dùng chung</Pill>}
      {doc.status && doc.status !== "used" && (
        <Pill tone={RULE_STATUS[doc.status]?.tone}>{RULE_STATUS[doc.status]?.label ?? doc.status}</Pill>
      )}
      {/* Partial or left out: the agent was told and could open the whole file. */}
      {doc.read === true && <Pill tone="good">agent đã mở file</Pill>}
      {doc.read === false && <span className="muted small"> · agent chưa mở file</span>}
      {doc.why?.length ? <span className="muted small"> ({doc.why.join("; ")})</span> : null}
    </span>
  );
}
