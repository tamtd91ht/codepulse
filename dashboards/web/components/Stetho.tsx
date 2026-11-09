"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { type FormEvent, useEffect, useLayoutEffect, useRef, useState } from "react";

import { Icon, IconButton } from "@/components/icons";
import { Markdown } from "@/components/Markdown";
import { Drawer } from "@/components/ui";
import { ApiError, api, useApi } from "@/lib/api";
import { askAssistant, type AssistantScreen, type AssistantStatus, type AssistantTurn } from "@/lib/assistant";

/**
 * Stetho, the AI assistant (app/assistant): a floating "Hỏi Stetho" button on every screen opens a
 * chat panel on the right. Admins and managers only (AppShell decides; the API refuses others).
 *
 * The conversation lives here, above the pages, so it survives navigation, and in sessionStorage
 * so a reload keeps it (this tab only). Each question carries the screen open when it was sent
 * (path, filters in the URL, title); the server puts that screen's data first in the context.
 * The answer streams in; "Dừng" or closing the tab stops the CLI on the server.
 */

interface Msg {
  id: string;
  role: "user" | "assistant";
  content: string;
  streaming?: boolean;
  error?: string;
  meta?: { model: string | null; seconds: number };
}

const STORE_KEY = "codepulse-stetho-chat";
/** The model this browser picked; "" = the server's default (the review model). */
const MODEL_KEY = "codepulse-stetho-model";
// The conversation's id (Lịch sử Stetho groups turns by it); a new one per "Cuộc trò chuyện mới".
const CONVERSATION_KEY = "codepulse-stetho-conversation";

function newConversation(): string {
  try {
    return crypto.randomUUID();
  } catch {
    return `${Date.now().toString(16)}-${Math.random().toString(16).slice(2, 10)}`;
  }
}
const MAX_QUESTION = 4000;

const TITLES: Record<string, string> = {
  "/overview": "Tổng quan",
  "/insights": "Luồng làm việc",
  "/teams": "Team",
  "/members": "Thành viên",
  "/reviews": "Kết quả review",
  "/reviews/request": "Yêu cầu review",
  "/jira": "Task Jira",
  "/stetho": "Tri thức Stetho",
  "/stetho/history": "Lịch sử Stetho",
  "/critical": "Critical Board",
  "/disputes": "Khiếu nại",
  "/admin": "Admin",
  "/guide": "Hướng dẫn",
};

const DETAIL: [RegExp, string][] = [
  [/^\/projects\/\d+\/knowledge$/, "Tri thức review"],
  [/^\/projects\/\d+$/, "Dự án"],
  [/^\/teams\/\d+$/, "Team"],
  [/^\/members\/\d+\/evidence$/, "Hồ sơ tháng"],
  [/^\/members\/\d+$/, "Thành viên"],
  [/^\/reviews\/\d+$/, "Review"],
];

function screenTitle(path: string): string {
  if (TITLES[path]) return TITLES[path];
  const kind = DETAIL.find(([re]) => re.test(path))?.[1];
  if (!kind) return "CodePulse";
  // The page's own heading names the thing (a dự án, a team, a person), when it has loaded.
  const heading = typeof document === "undefined" ? "" : document.querySelector("main h1")?.textContent?.trim();
  return heading && heading !== kind ? `${kind} ${heading}`.slice(0, 80) : kind;
}

function currentScreen(pathname: string): AssistantScreen {
  const path = pathname.replace(/\/+$/, "") || "/";
  const search = typeof window === "undefined" ? "" : window.location.search;
  return { path, search: search === "?" ? "" : search, title: screenTitle(path) };
}

function suggestions(path: string): string[] {
  if (/^\/reviews\/\d+$/.test(path))
    return ["Review này có vấn đề gì đáng lo nhất?", "Finding nào nên sửa trước, vì sao?", "Có finding nào có thể là false positive không?"];
  if (/^\/projects\/\d+$/.test(path))
    return ["Dự án này đang tốt lên hay xấu đi?", "Repo nào cần chú ý nhất?", "Loại lỗi nào lặp lại nhiều nhất?"];
  if (/^\/teams\/\d+$/.test(path))
    return ["Tóm tắt tình hình team kỳ này", "Team nên tập trung cải thiện điều gì?", "Điểm nóng nào cần xử lý trước?"];
  if (/^\/members\/\d+$/.test(path))
    return ["Xu hướng chất lượng 12 tuần qua thế nào?", "Nên coaching về loại lỗi nào?"];
  switch (path) {
    case "/critical":
      return ["Finding nào nên xử lý trước, vì sao?", "Các finding này có chung nguyên nhân không?", "Repo nào có nhiều finding nghiêm trọng nhất?"];
    case "/reviews":
      return ["Tóm tắt các review gần đây", "Review nào bị block hoặc phải sửa, vì sao?", "Có review lỗi nào cần chạy lại không?"];
    case "/disputes":
      return ["Khiếu nại nào nên duyệt trước?", "Có loại finding nào hay bị khiếu nại không?"];
    default:
      return ["Tóm tắt chất lượng code tuần này", "Dự án nào cần chú ý nhất lúc này?", "Team nào đang tiến bộ, team nào đi xuống?"];
  }
}

const newId = () => Math.random().toString(36).slice(2, 10);

function load<T>(storage: () => Storage, key: string, fallback: T): T {
  try {
    const raw = storage().getItem(key);
    return raw === null ? fallback : (JSON.parse(raw) as T);
  } catch {
    return fallback;
  }
}

function save(storage: () => Storage, key: string, value: unknown): void {
  try {
    storage().setItem(key, JSON.stringify(value));
  } catch {
    /* storage blocked or full: the chat lives in memory only */
  }
}

export function Stetho() {
  const pathname = usePathname();
  const { data: status } = useApi<AssistantStatus>("/assistant/status");
  const [open, setOpen] = useState(false);
  const [msgs, setMsgs] = useState<Msg[]>([]);
  const [draft, setDraft] = useState("");
  const [model, setModelState] = useState("");
  const [conversation, setConversation] = useState("");
  const abort = useRef<AbortController | null>(null);

  // Restored after mount (not in the initial state): the server render has no storage.
  useEffect(() => {
    const stored = load<Msg[]>(() => sessionStorage, STORE_KEY, []);
    // A turn still streaming when the page reloaded lost its connection: say so instead of hanging.
    setMsgs(stored.map((m) => (m.streaming ? { ...m, streaming: false, error: m.error ?? "Đã ngắt khi tải lại trang" } : m)));
    setModelState(load<string>(() => localStorage, MODEL_KEY, ""));
    const kept = load<string>(() => sessionStorage, CONVERSATION_KEY, "");
    const id = kept || newConversation();
    setConversation(id);
    save(() => sessionStorage, CONVERSATION_KEY, id);
  }, []);
  const startOver = () => {
    const id = newConversation();
    setConversation(id);
    save(() => sessionStorage, CONVERSATION_KEY, id);
  };
  useEffect(() => save(() => sessionStorage, STORE_KEY, msgs.slice(-40)), [msgs]);

  const busy = msgs.some((m) => m.streaming);
  const name = status?.name ?? "Stetho";
  // A remembered model the server no longer offers falls back to the default.
  const activeModel = status?.models.some((m) => m.id === model) ? model : "";

  const setModel = (value: string) => {
    setModelState(value);
    save(() => localStorage, MODEL_KEY, value);
  };

  const ask = async (text: string) => {
    const question = text.trim();
    if (!question || busy) return;
    const history: AssistantTurn[] = msgs.filter((m) => !m.error && m.content).map((m) => ({ role: m.role, content: m.content }));
    const answerId = newId();
    setMsgs((list) => [...list, { id: newId(), role: "user", content: question }, { id: answerId, role: "assistant", content: "", streaming: true }]);
    setDraft("");
    const controller = new AbortController();
    abort.current = controller;
    const patch = (fn: (m: Msg) => Msg) => setMsgs((list) => list.map((m) => (m.id === answerId ? fn(m) : m)));
    try {
      const done = await askAssistant(
        {
          question,
          history,
          screen: currentScreen(pathname),
          ...(activeModel ? { model: activeModel } : {}),
          ...(conversation ? { conversation_id: conversation } : {}),
        },
        { signal: controller.signal, onDelta: (piece) => patch((m) => ({ ...m, content: m.content + piece })) },
      );
      patch((m) => ({ ...m, streaming: false, meta: { model: done.model, seconds: done.duration_ms / 1000 } }));
    } catch (e) {
      const stopped = (e as Error).name === "AbortError";
      patch((m) => ({ ...m, streaming: false, error: stopped ? "Đã dừng" : (e as Error).message }));
    } finally {
      abort.current = null;
    }
  };

  const screen = currentScreen(pathname);

  return (
    <>
      {!open && (
        <button
          type="button"
          className="stetho-fab"
          onClick={() => setOpen(true)}
          aria-label={`Hỏi ${name} về màn hình ${screen.title}`}
          title={`Hỏi ${name} về: ${screen.title}`}
        >
          <Icon name="stethoscope" size={18} />
          <span>Hỏi {name}</span>
          {busy && <span className="spinner inline" aria-hidden="true" />}
        </button>
      )}
      {open && (
        <Drawer
          width={480}
          onClose={() => setOpen(false)}
          title={
            <span className="stetho-title">
              <Icon name="stethoscope" size={18} /> {name}
            </span>
          }
          actions={
            <>
              {!!status?.models.length && (
                <select
                  className="stetho-model"
                  aria-label={`Model của ${name}`}
                  value={activeModel}
                  disabled={busy}
                  onChange={(e) => setModel(e.target.value)}
                  title={status.models.find((m) => m.id === activeModel)?.hint ?? "Model review đặt ở Admin"}
                >
                  <option value="">Mặc định ({status.default_model})</option>
                  {status.models.map((m) => (
                    <option key={m.id} value={m.id}>
                      {m.label} — {m.hint}
                    </option>
                  ))}
                </select>
              )}
              {status?.can_train && (
                <Link href="/stetho" className="icon-btn" aria-label={`Tri thức của ${name}`} data-tip={`Tri thức của ${name}`} onClick={() => setOpen(false)}>
                  <Icon name="book" size={16} />
                </Link>
              )}
              {msgs.length > 0 && (
                <IconButton
                  icon="plus"
                  label="Cuộc trò chuyện mới"
                  onClick={() => {
                    abort.current?.abort();
                    setMsgs([]);
                    startOver();
                  }}
                />
              )}
            </>
          }
        >
          <Chat
            name={name}
            status={status}
            msgs={msgs}
            draft={draft}
            setDraft={setDraft}
            busy={busy}
            screen={screen}
            onAsk={(q) => void ask(q)}
            onStop={() => abort.current?.abort()}
          />
        </Drawer>
      )}
    </>
  );
}

/** "Lưu thành tri thức": a trainer corrects an answer and keeps it; the same question then
 * finds it (docs/specs/28). */
function SaveAnswer({ question, answer, onDone }: { question: string; answer: string; onDone: () => void }) {
  const [title, setTitle] = useState(question.slice(0, 200));
  const [body, setBody] = useState(answer);
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  const [everyone, setEveryone] = useState(false);
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setProblem(null);
    try {
      await api.post("/assistant/docs", {
        title: title.trim(),
        body,
        source: "qa",
        question,
        audience: everyone ? "all" : "trainers",
      });
      setSaved(true);
      setTimeout(onDone, 1200);
    } catch (err) {
      setProblem(err instanceof ApiError ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };
  if (saved) return <div className="small good stetho-saved">Đã lưu vào tri thức Stetho.</div>;
  return (
    <form className="stetho-save" onSubmit={(e) => void submit(e)}>
      <span className="small muted">Sửa cho đúng rồi lưu: lần sau Stetho trả lời theo nội dung này.</span>
      <input value={title} onChange={(e) => setTitle(e.target.value)} maxLength={200} required aria-label="Tiêu đề tri thức" />
      <textarea rows={8} value={body} onChange={(e) => setBody(e.target.value)} required aria-label="Nội dung tri thức" />
      <label className="small stetho-save-who">
        <input type="checkbox" checked={everyone} onChange={(e) => setEveryone(e.target.checked)} />
        Cho mọi người dùng Stetho đọc (mặc định chỉ người dạy Stetho)
      </label>
      {everyone && (
        <div className="small tone-warn">Câu trả lời này dựa trên số liệu bạn được xem: xoá tên người, điểm, finding trước khi chia sẻ cho mọi người.</div>
      )}
      {problem && <div className="error small">{problem}</div>}
      <div className="toolbar">
        <button type="submit" className="sm primary" disabled={busy || !body.trim() || !title.trim()}>
          {busy ? "Đang lưu…" : "Lưu thành tri thức"}
        </button>
        <button type="button" className="sm ghost" onClick={onDone}>
          Huỷ
        </button>
      </div>
    </form>
  );
}

function Chat({
  name,
  status,
  msgs,
  draft,
  setDraft,
  busy,
  screen,
  onAsk,
  onStop,
}: {
  name: string;
  status: AssistantStatus | undefined;
  msgs: Msg[];
  draft: string;
  setDraft: (v: string) => void;
  busy: boolean;
  screen: AssistantScreen;
  onAsk: (q: string) => void;
  onStop: () => void;
}) {
  const list = useRef<HTMLDivElement>(null);
  const input = useRef<HTMLTextAreaElement>(null);
  // Follow the answer unless the reader scrolled up; a new question always scrolls to the bottom.
  const stick = useRef(true);
  const count = useRef(msgs.length);
  const [copied, setCopied] = useState<string | null>(null);
  const [saving, setSaving] = useState<string | null>(null);

  useEffect(() => {
    // After the drawer focused its close button: the question box is what one types into.
    const id = requestAnimationFrame(() => input.current?.focus());
    return () => cancelAnimationFrame(id);
  }, []);

  useLayoutEffect(() => {
    if (msgs.length > count.current) stick.current = true;
    count.current = msgs.length;
    const el = list.current;
    if (el && stick.current) el.scrollTop = el.scrollHeight;
  }, [msgs]);

  const unavailable = status !== undefined && !status.available;
  const send = () => {
    if (draft.trim() && !busy && !unavailable) onAsk(draft);
  };
  const copy = (id: string, text: string) =>
    void navigator.clipboard?.writeText(text).then(
      () => {
        setCopied(id);
        setTimeout(() => setCopied((c) => (c === id ? null : c)), 1500);
      },
      () => undefined,
    );

  return (
    <div className="stetho">
      <div className="stetho-focus small muted">Đang xem: {screen.title}</div>
      <div
        className="stetho-list"
        ref={list}
        aria-live="polite"
        onScroll={(e) => {
          const el = e.currentTarget;
          stick.current = el.scrollHeight - el.scrollTop - el.clientHeight < 48;
        }}
      >
        {!status ? (
          <p className="muted small">Đang kiểm tra kết nối…</p>
        ) : unavailable ? (
          <div className="stetho-empty">
            <h3>{name} chưa sẵn sàng</h3>
            <p>{status.reason}</p>
          </div>
        ) : msgs.length === 0 ? (
          <div className="stetho-empty">
            <h3>Hỏi về {screen.title}</h3>
            <p>
              {name} đọc số liệu của màn hình bạn đang mở trước, rồi tới tổng quan các dự án và team bạn được xem để phân
              tích. {name} chỉ thấy những gì bạn được xem, không đọc mã nguồn và không thay đổi gì.
            </p>
            <p className="small muted stetho-privacy">
              Câu hỏi của bạn và vài ý chính của câu trả lời được lưu 30 ngày để admin cải thiện {name}; nội dung đầy đủ không lưu.
            </p>
            <div className="stetho-suggest">
              {suggestions(screen.path).map((s) => (
                <button key={s} type="button" onClick={() => onAsk(s)}>
                  {s}
                </button>
              ))}
            </div>
          </div>
        ) : (
          msgs.map((m, i) =>
            m.role === "user" ? (
              <div key={m.id} className="stetho-msg user">
                <div className="stetho-bubble">{m.content}</div>
              </div>
            ) : (
              <div key={m.id} className="stetho-msg assistant" aria-busy={m.streaming}>
                {m.content ? <Markdown text={m.content} /> : m.streaming ? <span className="stetho-typing" aria-label="Đang trả lời" /> : null}
                {m.error && (
                  <div className="error small" role="alert">
                    {m.error}
                  </div>
                )}
                {!m.streaming && m.content && (
                  <div className="stetho-meta small muted">
                    {m.meta?.model && <span>{m.meta.model}</span>}
                    {m.meta && <span>{m.meta.seconds.toFixed(1)} giây</span>}
                    <IconButton icon={copied === m.id ? "check" : "copy"} size={14} label="Sao chép câu trả lời" onClick={() => copy(m.id, m.content)} />
                    {status?.can_train && (
                      <IconButton
                        icon="book"
                        size={14}
                        label="Lưu thành tri thức (sửa rồi lưu)"
                        onClick={() => setSaving((s) => (s === m.id ? null : m.id))}
                      />
                    )}
                  </div>
                )}
                {saving === m.id && (
                  <SaveAnswer
                    question={msgs[i - 1]?.role === "user" ? msgs[i - 1].content : ""}
                    answer={m.content}
                    onDone={() => setSaving(null)}
                  />
                )}
              </div>
            ),
          )
        )}
      </div>
      <div className="stetho-compose">
        <textarea
          ref={input}
          rows={2}
          aria-label={`Câu hỏi cho ${name}`}
          placeholder={unavailable ? `${name} chưa sẵn sàng` : `Hỏi về ${screen.title}… (Enter để gửi, Shift+Enter xuống dòng)`}
          maxLength={MAX_QUESTION}
          value={draft}
          disabled={unavailable}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
              e.preventDefault();
              send();
            }
          }}
        />
        {busy ? (
          <IconButton icon="stop" label="Dừng trả lời" onClick={onStop} />
        ) : (
          <button type="button" className="primary ink stetho-send" onClick={send} disabled={!draft.trim() || unavailable} aria-label="Gửi câu hỏi">
            <Icon name="arrowUp" size={18} />
          </button>
        )}
      </div>
    </div>
  );
}
