import { ApiError } from "./api";

/** GET /assistant/status (admins and managers only). */
export interface AssistantStatus {
  name: string;
  available: boolean;
  /** Why it is not available (no Claude connection, budget used up). */
  reason: string | null;
  models: { id: string; label: string; hint: string }[];
  /** The review model set on Admin: what "Mặc định" runs. */
  default_model: string;
}

export interface AssistantTurn {
  role: "user" | "assistant";
  content: string;
}

/** The page the question is about: its path, query string (filters) and title. */
export interface AssistantScreen {
  path: string;
  search: string;
  title: string;
}

export interface AssistantDone {
  model: string | null;
  cost_usd: number | null;
  duration_ms: number;
}

/**
 * Ask Stetho (POST /assistant/chat, Server-Sent Events): `onDelta` gets each piece of the answer,
 * the promise resolves when it is done. A refusal before the stream starts is an ApiError with the
 * HTTP status; a failure mid-stream is an ApiError too (what arrived stays with the caller).
 * Aborting `signal` rejects with AbortError and the server kills the CLI.
 */
export async function askAssistant(
  body: { question: string; history: AssistantTurn[]; screen: AssistantScreen; model?: string },
  { onDelta, signal }: { onDelta: (text: string) => void; signal?: AbortSignal },
): Promise<AssistantDone> {
  let response: Response;
  try {
    response = await fetch("/api/v1/assistant/chat", {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
      body: JSON.stringify(body),
      signal,
    });
  } catch (e) {
    if ((e as Error).name === "AbortError") throw e;
    throw new ApiError(0, "Không kết nối được tới máy chủ");
  }
  if (!response.ok || !response.body) {
    let detail = response.statusText || `HTTP ${response.status}`;
    try {
      const data = (await response.json()) as { detail?: unknown };
      if (typeof data.detail === "string") detail = data.detail;
    } catch {
      /* not JSON */
    }
    throw new ApiError(response.status, detail);
  }
  const reader = response.body.pipeThrough(new TextDecoderStream()).getReader();
  let buffer = "";
  let done: AssistantDone | null = null;
  let error: string | null = null;
  for (;;) {
    const { value, done: end } = await reader.read();
    if (end) break;
    buffer += value;
    let cut: number;
    while ((cut = buffer.indexOf("\n\n")) >= 0) {
      const block = buffer.slice(0, cut);
      buffer = buffer.slice(cut + 2);
      let event = "message";
      let data = "";
      for (const line of block.split("\n")) {
        if (line.startsWith("event:")) event = line.slice(6).trim();
        else if (line.startsWith("data:")) data += line.slice(5).trim();
      }
      if (!data) continue; // ": ping"
      const parsed = JSON.parse(data) as { text?: string; error?: string } & AssistantDone;
      if (event === "delta" && typeof parsed.text === "string") onDelta(parsed.text);
      else if (event === "done") done = { model: parsed.model, cost_usd: parsed.cost_usd, duration_ms: parsed.duration_ms };
      else if (event === "error") error = parsed.error ?? "Stetho không trả lời được";
    }
  }
  if (error) throw new ApiError(502, error);
  if (!done) throw new ApiError(502, "Mất kết nối khi đang nhận câu trả lời");
  return done;
}
