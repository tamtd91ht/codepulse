"use client";

import { useEffect, useRef, useState } from "react";

import { Icon } from "@/components/icons";
import { Alert, Pill } from "@/components/ui";
import { api } from "@/lib/api";
import {
  SESSION_NOTE_KINDS,
  SESSION_NOTE_LABEL,
  type Session,
  type SessionNoteKind,
  type TestDetail,
  clockText,
  durationText,
  errorText,
  sessionElapsed,
} from "@/lib/testruns";

/*
 * Exploratory session of a test (docs/specs/30 §15.12 G8, API §15.16): the charter, a timer
 * (start / pause / resume) against the timebox, quick notes typed as note / bug / question / idea,
 * and their timeline. Recording the result copies the notes into it (and the session time when no
 * elapsed time is typed).
 */

const KIND_TONE: Record<SessionNoteKind, "bad" | "warn" | "info" | undefined> = { note: undefined, bug: "bad", question: "warn", idea: "info" };

/** The server answers with the session, or with the test holding it. */
function sessionOf(got: unknown): Session | null {
  if (!got || typeof got !== "object") return null;
  if ("notes" in got || "running" in got) return got as Session;
  if ("session" in got) return ((got as { session?: Session | null }).session ?? null) as Session | null;
  return null;
}

export function ExploratorySession({ detail, readOnly, onChanged }: { detail: TestDetail; readOnly: boolean; onChanged: () => void }) {
  const [local, setLocal] = useState<Session | null>(null);
  const [now, setNow] = useState(() => Date.now());
  const [kind, setKind] = useState<SessionNoteKind>("note");
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const input = useRef<HTMLInputElement>(null);

  useEffect(() => setLocal(null), [detail.session]);
  const s = local ?? detail.session ?? null;
  const running = !!s?.running;
  useEffect(() => {
    if (!running) return;
    setNow(Date.now());
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, [running]);

  const elapsed = sessionElapsed(s, now);
  const timebox = detail.content.estimate_s ?? 0;
  const over = !!timebox && elapsed > timebox;
  const started = !!s && (!!s.started_at || s.elapsed_s > 0 || s.notes.length > 0);

  const act = async (action: "start" | "pause" | "resume") => {
    setBusy(true);
    setError(null);
    try {
      const got = sessionOf(await api.post<unknown>(`/testcases/tests/${detail.id}/session`, { action }));
      if (got) setLocal(got);
      setNow(Date.now());
      onChanged();
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  };

  const addNote = async () => {
    const t = text.trim();
    if (!t) return;
    setBusy(true);
    setError(null);
    try {
      const got = sessionOf(await api.post<unknown>(`/testcases/tests/${detail.id}/session/notes`, { kind, text: t }));
      if (got) setLocal(got);
      setText("");
      onChanged();
      input.current?.focus();
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  };

  const notes = [...(s?.notes ?? [])].sort((a, b) => (a.offset_s ?? 0) - (b.offset_s ?? 0));
  const pct = timebox ? Math.min(100, Math.round((elapsed / timebox) * 100)) : 0;
  return (
    <section className="ses" aria-label="Phiên khám phá">
      <div className="tr-block">
        <h4>Sứ mệnh (charter)</h4>
        <div className="tr-pre">{detail.content.charter || <span className="muted">Testcase chưa ghi sứ mệnh.</span>}</div>
      </div>
      <div className="card ses-timer">
        <div className="ses-clock-row">
          <span className={`ses-clock${over ? " over" : ""}`} role="timer" aria-live="off" aria-label={`Đã chạy ${clockText(elapsed)}`}>
            {clockText(elapsed)}
          </span>
          <span className="small muted">
            {timebox ? `/ timebox ${durationText(timebox)}` : "Chưa đặt timebox"}
            {over ? " · quá timebox" : ""}
          </span>
          {running && <Pill tone="good">Đang chạy</Pill>}
          {!running && started && <Pill>Tạm dừng</Pill>}
          <span className="spacer" />
          {!readOnly &&
            (running ? (
              <button type="button" className="sm" onClick={() => void act("pause")} disabled={busy}>
                <Icon name="stop" size={14} /> Tạm dừng
              </button>
            ) : started ? (
              <button type="button" className="sm primary ink" onClick={() => void act("resume")} disabled={busy}>
                <Icon name="activity" size={14} /> Tiếp tục
              </button>
            ) : (
              <button type="button" className="sm primary ink" onClick={() => void act("start")} disabled={busy}>
                <Icon name="activity" size={14} /> Bắt đầu phiên
              </button>
            ))}
        </div>
        {timebox > 0 && (
          <div className={`ses-bar${over ? " over" : ""}`} role="progressbar" aria-label="Thời gian so với timebox" aria-valuemin={0} aria-valuemax={100} aria-valuenow={pct}>
            <span style={{ width: `${pct}%` }} />
          </div>
        )}
      </div>
      {error && (
        <Alert tone="error" onClose={() => setError(null)}>
          {error}
        </Alert>
      )}
      {!readOnly && (
        <form
          className="ses-note-form"
          onSubmit={(e) => {
            e.preventDefault();
            void addNote();
          }}
        >
          <div className="tr-chips" role="group" aria-label="Loại ghi chú">
            {SESSION_NOTE_KINDS.map((k) => (
              <button key={k} type="button" className="tr-chip" aria-pressed={kind === k} onClick={() => setKind(k)}>
                {SESSION_NOTE_LABEL[k]}
              </button>
            ))}
          </div>
          <div className="tc-inline">
            <label className="ses-note-input">
              <span className="sr-only">Ghi nhanh ({SESSION_NOTE_LABEL[kind]})</span>
              <input ref={input} value={text} maxLength={2000} onChange={(e) => setText(e.target.value)} placeholder={`${SESSION_NOTE_LABEL[kind]}: ghi nhanh rồi Enter…`} />
            </label>
            <button type="submit" className="sm" disabled={busy || !text.trim()}>
              <Icon name="plus" size={14} /> Ghi
            </button>
          </div>
          {!started && <p className="small muted">Ghi chú gắn mốc thời gian theo phiên; bấm “Bắt đầu phiên” để bấm giờ.</p>}
        </form>
      )}
      <div className="tr-block">
        <h4>Dòng thời gian ({notes.length})</h4>
        {!notes.length ? (
          <p className="small muted">Chưa có ghi chú nào trong phiên.</p>
        ) : (
          <ol className="ses-timeline">
            {notes.map((n, i) => (
              <li key={`${n.at ?? ""}-${i}`}>
                <span className="ses-offset">{clockText(n.offset_s ?? 0)}</span>
                <Pill tone={KIND_TONE[n.kind as SessionNoteKind]}>{SESSION_NOTE_LABEL[n.kind as SessionNoteKind] ?? n.kind}</Pill>
                <span className="ses-text">{n.text}</span>
                {n.user_name && <span className="small muted">{n.user_name}</span>}
              </li>
            ))}
          </ol>
        )}
      </div>
    </section>
  );
}
