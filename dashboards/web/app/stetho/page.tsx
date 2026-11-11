"use client";

import Link from "next/link";
import { type FormEvent, useRef, useState } from "react";

import { Icon, IconButton } from "@/components/icons";
import { Markdown } from "@/components/Markdown";
import { Alert, Card, Drawer, EmptyState, Loading, PageHeader, Pill } from "@/components/ui";
import { ApiError, api, can, useApi } from "@/lib/api";
import { relative } from "@/lib/format";
import type { Me } from "@/lib/types";

/*
 * Tri thức Stetho (docs/specs/28, user 2026-11-09): the documents Stetho answers from. Files
 * (.md, .txt, .docx, .xlsx) and web links are converted to text and indexed; documents can be
 * written here, answers saved from the chat, the built-in CodePulse how-tos switched off, and the
 * web lookup switched on. Needs `assistant.train` (admins by default).
 */

type Audience = "all" | "trainers";

interface Doc {
  id: number;
  title: string;
  source: "builtin" | "manual" | "file" | "url" | "qa";
  source_name: string | null;
  audience: Audience;
  enabled: boolean;
  builtin: boolean;
  chars: number;
  chunks: number;
  updated_at: string | null;
  updated_by: string | null;
  question: string | null;
  body?: string;
}

interface DocList {
  docs: Doc[];
  web: boolean;
  file_kinds: string[];
  max_file_mb: number;
}

interface Hit {
  doc_id: number;
  title: string;
  heading: string | null;
  body: string;
  source: Doc["source"];
  score: number;
}

const SOURCE: Record<Doc["source"], { label: string; tone?: "info" | "good" | "warn" | "primary" }> = {
  builtin: { label: "Có sẵn", tone: "primary" },
  file: { label: "File", tone: "info" },
  url: { label: "Link", tone: "info" },
  manual: { label: "Viết tay" },
  qa: { label: "Câu trả lời", tone: "good" },
};

function Editor({ doc, onClose, onSaved }: { doc: Doc | "new"; onClose: () => void; onSaved: () => void }) {
  const isNew = doc === "new";
  const { data, error } = useApi<Doc>(isNew ? null : `/assistant/docs/${doc.id}`);
  const current = isNew ? null : data;
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const [preview, setPreview] = useState(false);
  const [body, setBody] = useState<string | null>(null);
  const text = body ?? current?.body ?? "";
  const readOnly = !isNew && doc.builtin;

  const submit = async (e: FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    const form = new FormData(e.currentTarget);
    const payload = {
      title: String(form.get("title") ?? "").trim(),
      body: text,
      audience: String(form.get("audience") ?? "all") as Audience,
    };
    setBusy(true);
    setProblem(null);
    try {
      if (isNew) await api.post("/assistant/docs", payload);
      else await api.patch(`/assistant/docs/${doc.id}`, payload);
      onSaved();
      onClose();
    } catch (err) {
      setProblem(err instanceof ApiError ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Drawer title={isNew ? "Viết tài liệu" : doc.title} onClose={onClose} width="wide">
      {!isNew && !current ? (
        <Loading error={error} rows={6} />
      ) : (
        <form className="kb-editor" onSubmit={(e) => void submit(e)}>
          {readOnly && <Alert tone="info">Tài liệu có sẵn của CodePulse cập nhật theo phiên bản, chỉ bật/tắt được.</Alert>}
          {current?.question && (
            <p className="muted small">
              Câu hỏi gốc: <em>{current.question}</em>
            </p>
          )}
          <label className="kb-field">
            <span className="small muted">Tiêu đề</span>
            <input name="title" required maxLength={200} defaultValue={current?.title ?? ""} disabled={readOnly} />
          </label>
          <label className="kb-field kb-inline">
            <span className="small muted">Ai được Stetho đọc cho</span>
            <select name="audience" defaultValue={current?.audience ?? "all"} disabled={readOnly}>
              <option value="all">Mọi người dùng Stetho</option>
              <option value="trainers">Chỉ người dạy Stetho</option>
            </select>
          </label>
          <div className="kb-field">
            <div className="kb-body-head">
              <span className="small muted">Nội dung (Markdown: # tiêu đề, - gạch đầu dòng, ``` code)</span>
              <button type="button" className="sm ghost" onClick={() => setPreview((p) => !p)}>
                {preview ? "Sửa" : "Xem trước"}
              </button>
            </div>
            {preview ? (
              <div className="kb-preview">
                <Markdown text={text || "_(trống)_"} />
              </div>
            ) : (
              <textarea rows={22} value={text} onChange={(e) => setBody(e.target.value)} required disabled={readOnly} />
            )}
          </div>
          {problem && <Alert tone="error">{problem}</Alert>}
          {!readOnly && (
            <div className="toolbar">
              <button type="submit" className="primary" disabled={busy || !text.trim()}>
                {busy ? "Đang lưu…" : isNew ? "Thêm vào tri thức" : "Lưu"}
              </button>
              <button type="button" onClick={onClose}>
                Huỷ
              </button>
            </div>
          )}
        </form>
      )}
    </Drawer>
  );
}

function TryIt() {
  const [question, setQuestion] = useState("");
  const [result, setResult] = useState<{ terms: string[]; hits: Hit[] } | null>(null);
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const run = async (e: FormEvent) => {
    e.preventDefault();
    if (!question.trim()) return;
    setBusy(true);
    setProblem(null);
    try {
      setResult(await api.post("/assistant/docs/search", { question }));
    } catch (err) {
      setProblem(err instanceof ApiError ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };
  return (
    <Card title="Thử hỏi" subtitle="Xem Stetho sẽ đọc những đoạn tài liệu nào cho một câu hỏi (không gọi LLM, không tốn hạn mức)." collapsible={false}>
      <form className="toolbar kb-try" onSubmit={(e) => void run(e)}>
        <input value={question} onChange={(e) => setQuestion(e.target.value)} placeholder="vd. Commit sửa bug phải ghi gì?" aria-label="Câu hỏi thử" />
        <button type="submit" disabled={busy || !question.trim()}>
          {busy ? "Đang tìm…" : "Tìm"}
        </button>
      </form>
      {problem && <Alert tone="error">{problem}</Alert>}
      {result && (
        <div className="kb-hits">
          <p className="muted small">Từ khoá: {result.terms.join(", ") || "—"}</p>
          {result.hits.length === 0 ? (
            <p className="muted">Không đoạn nào khớp: Stetho sẽ trả lời là tài liệu chưa có. Thêm tài liệu về chủ đề này.</p>
          ) : (
            <ol>
              {result.hits.map((h, i) => (
                <li key={`${h.doc_id}-${i}`}>
                  <div className="kb-hit-head">
                    <b>{h.title}</b>
                    {h.heading && <span className="muted small"> › {h.heading}</span>}
                    <Pill tone={SOURCE[h.source].tone}>{SOURCE[h.source].label}</Pill>
                    <span className="muted small">điểm {h.score}</span>
                  </div>
                  <pre className="kb-hit-body">{h.body.length > 600 ? `${h.body.slice(0, 600)}…` : h.body}</pre>
                </li>
              ))}
            </ol>
          )}
        </div>
      )}
    </Card>
  );
}

export default function StethoKnowledgePage() {
  const { data: me } = useApi<Me>("/auth/me");
  const list = useApi<DocList>(me && can(me, "assistant.train") ? "/assistant/docs" : null);
  const [audience, setAudience] = useState<Audience>("all");
  const [url, setUrl] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const [editing, setEditing] = useState<Doc | "new" | null>(null);
  const [removing, setRemoving] = useState<number | null>(null);
  const files = useRef<HTMLInputElement>(null);

  async function run(key: string, action: () => Promise<void>, done?: string) {
    setBusy(key);
    setProblem(null);
    setNotice(null);
    try {
      await action();
      if (done) setNotice(done);
      await list.mutate();
    } catch (err) {
      setProblem(err instanceof ApiError ? err.message : String(err));
    } finally {
      setBusy(null);
    }
  }

  const upload = (picked: FileList | null) => {
    if (!picked?.length) return;
    const all = Array.from(picked);
    void run(
      "upload",
      async () => {
        const failed: string[] = [];
        for (const f of all) {
          const res = await fetch(`/api/v1/assistant/docs/upload?filename=${encodeURIComponent(f.name)}&audience=${audience}`, {
            method: "POST",
            body: f,
            credentials: "include",
            headers: { "Content-Type": "application/octet-stream" },
          });
          if (!res.ok) {
            const detail = await res.json().catch(() => null);
            failed.push(`${f.name}: ${detail?.detail ?? res.status}`);
          }
        }
        if (files.current) files.current.value = "";
        if (failed.length) throw new Error(failed.join(" · "));
      },
      all.length > 1 ? `Đã thêm ${all.length} file.` : `Đã thêm ${all[0].name}.`,
    );
  };

  if (me && !can(me, "assistant.train"))
    return (
      <>
        <PageHeader title="Tri thức Stetho" />
        <Card collapsible={false}>
          <EmptyState icon="alert" title="Bạn chưa có quyền dạy Stetho">
            Quyền “Dạy Stetho” (assistant.train) do admin cấp ở Admin › Phân quyền.
          </EmptyState>
        </Card>
      </>
    );

  const d = list.data;
  return (
    <>
      <PageHeader
        eyebrow="Stetho"
        title="Tri thức Stetho"
        subtitle="Tài liệu Stetho dựa vào để trả lời: quy trình, chuẩn, hướng dẫn nội bộ. Stetho trả lời đúng theo nội dung ở đây và ghi nguồn."
        actions={
          <button className="primary" onClick={() => setEditing("new")}>
            <Icon name="plus" size={14} /> Viết tài liệu
          </button>
        }
      />
      {problem && <Alert tone="error">{problem}</Alert>}
      {notice && <Alert tone="success">{notice}</Alert>}

      <div className="grid two kb-top">
        <Card title="Nhập tài liệu" collapsible={false}>
          <div className="kb-import">
            <label className="kb-field kb-inline">
              <span className="small muted">Ai được Stetho đọc cho</span>
              <select value={audience} onChange={(e) => setAudience(e.target.value as Audience)}>
                <option value="all">Mọi người dùng Stetho</option>
                <option value="trainers">Chỉ người dạy Stetho</option>
              </select>
            </label>
            <div className="toolbar">
              <input
                ref={files}
                type="file"
                multiple
                hidden
                accept={(d?.file_kinds ?? [".md", ".txt", ".docx", ".xlsx"]).join(",")}
                onChange={(e) => upload(e.target.files)}
              />
              <button onClick={() => files.current?.click()} disabled={busy !== null}>
                <Icon name="download" size={14} /> {busy === "upload" ? "Đang nhập…" : "Tải file lên"}
              </button>
              <span className="muted small">.md, .txt, .doc, .docx, .xlsx · tối đa {d?.max_file_mb ?? 10} MB mỗi file</span>
            </div>
            <form
              className="toolbar"
              onSubmit={(e) => {
                e.preventDefault();
                void run(
                  "url",
                  async () => {
                    await api.post("/assistant/docs/url", { url, audience });
                    setUrl("");
                  },
                  "Đã lấy nội dung từ link.",
                );
              }}
            >
              <input
                type="url"
                required
                value={url}
                onChange={(e) => setUrl(e.target.value)}
                placeholder="https://wiki.congty.vn/quy-trinh-release"
                aria-label="Link tài liệu"
                className="kb-url"
              />
              <button type="submit" disabled={busy !== null || !url.trim()}>
                {busy === "url" ? "Đang lấy…" : "Thêm từ link"}
              </button>
            </form>
            <p className="muted small">
              Chỉ giữ phần chữ (file gốc không lưu). Link phải mở được từ máy chủ CodePulse, không cần đăng nhập (trang cần đăng nhập thì tải file
              lên thay). Wiki nội bộ (địa chỉ IP nội bộ) chỉ lấy được khi admin thêm tên máy vào biến ASSISTANT_URL_HOSTS.
            </p>
          </div>
        </Card>

        <Card title="Tra web" collapsible={false}>
          <label className="kb-switch">
            <input
              type="checkbox"
              checked={d?.web ?? false}
              disabled={!d || busy !== null}
              onChange={(e) => void run("web", async () => void (await api.put("/assistant/settings", { web: e.target.checked })))}
            />
            <span>Cho Stetho tìm kiếm web khi tài liệu không trả lời được</span>
          </label>
          <p className="muted small">
            Stetho chỉ dùng công cụ tìm kiếm (chạy phía Anthropic, không mở trang hay địa chỉ nội bộ từ máy chủ CodePulse), cho kiến thức chung
            (thư viện, công nghệ, chuẩn công khai), ghi link nguồn và được dặn không đưa dữ liệu nội bộ (tên người, dự án, code, số liệu) vào câu
            tìm kiếm. Câu tìm kiếm vẫn đi ra ngoài, nên chỉ bật khi chấp nhận điều đó. Mỗi câu trả lời có tra web chậm hơn và tốn hạn mức hơn.
          </p>
        </Card>
      </div>

      <Card title="Tài liệu" subtitle={d ? `${d.docs.filter((x) => x.enabled).length} đang dùng / ${d.docs.length}` : undefined} collapsible={false} flush>
        {!d ? (
          <Loading error={list.error} rows={5} />
        ) : d.docs.length === 0 ? (
          <EmptyState icon="book" title="Chưa có tài liệu" />
        ) : (
          <div className="table-wrap">
            <table className="kb-table">
              <thead>
                <tr>
                  <th>Tài liệu</th>
                  <th>Nguồn</th>
                  <th>Ai đọc</th>
                  <th className="num">Đoạn</th>
                  <th>Cập nhật</th>
                  <th>Dùng</th>
                  <th aria-label="Thao tác" />
                </tr>
              </thead>
              <tbody>
                {d.docs.map((doc) => (
                  <tr key={doc.id} className={doc.enabled ? undefined : "kb-off"}>
                    <td>
                      <button type="button" className="link kb-title" onClick={() => setEditing(doc)}>
                        {doc.title}
                      </button>
                      {doc.source_name && doc.source !== "builtin" && (
                        <div className="muted small kb-origin">
                          {doc.source === "url" ? (
                            <a href={doc.source_name} target="_blank" rel="noopener noreferrer">
                              {doc.source_name}
                            </a>
                          ) : (
                            doc.source_name
                          )}
                        </div>
                      )}
                      {removing === doc.id && (
                        <div className="kb-confirm">
                          Xoá “{doc.title}”?
                          <button type="button" className="sm danger" onClick={() => void run(`rm-${doc.id}`, async () => void (await api.delete(`/assistant/docs/${doc.id}`)), "Đã xoá.")}>
                            Xoá
                          </button>
                          <button type="button" className="sm ghost" onClick={() => setRemoving(null)}>
                            Huỷ
                          </button>
                        </div>
                      )}
                    </td>
                    <td>
                      <Pill tone={SOURCE[doc.source].tone}>{SOURCE[doc.source].label}</Pill>
                    </td>
                    <td className="small">{doc.audience === "all" ? "Mọi người" : "Người dạy"}</td>
                    <td className="num">{doc.chunks}</td>
                    <td className="small muted">
                      {doc.updated_at ? relative(doc.updated_at) : "—"}
                      {doc.updated_by && <div>{doc.updated_by}</div>}
                    </td>
                    <td>
                      <input
                        type="checkbox"
                        aria-label={`Dùng ${doc.title}`}
                        checked={doc.enabled}
                        disabled={busy !== null}
                        onChange={(e) => void run(`on-${doc.id}`, async () => void (await api.patch(`/assistant/docs/${doc.id}`, { enabled: e.target.checked })))}
                      />
                    </td>
                    <td className="kb-actions">
                      {doc.source === "url" && (
                        <IconButton
                          icon="sync"
                          label="Lấy lại từ link"
                          disabled={busy !== null}
                          onClick={() => void run(`re-${doc.id}`, async () => void (await api.post(`/assistant/docs/${doc.id}/refresh`)), "Đã lấy lại nội dung.")}
                        />
                      )}
                      <IconButton icon={doc.builtin ? "search" : "edit"} label={doc.builtin ? "Xem" : "Sửa"} onClick={() => setEditing(doc)} />
                      {!doc.builtin && <IconButton icon="remove" label="Xoá" tone="danger" onClick={() => setRemoving(doc.id)} />}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      <TryIt />

      <p className="muted small kb-foot">
        Dạy Stetho từ cuộc trò chuyện: dưới mỗi câu trả lời có nút <b>Lưu thành tri thức</b> — sửa lại cho đúng rồi lưu; lần sau Stetho trả lời
        theo bản đã sửa. Ai được dạy Stetho: quyền “Dạy Stetho” ở <Link href="/admin#permissions">Admin › Phân quyền</Link>.
      </p>

      {editing && <Editor key={editing === "new" ? "new" : editing.id} doc={editing} onClose={() => setEditing(null)} onSaved={() => void list.mutate()} />}
    </>
  );
}
