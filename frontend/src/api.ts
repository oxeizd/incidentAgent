import type {
  ThreadSummary,
  ThreadStateResponse,
  ChatHistoryMessage,
  SseEvent,
  InterruptToolDefinition,
  PresentationRecord,
  PresentationFields,
} from "./types";

const API_BASE = "";

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
    this.name = "ApiError";
  }
}

function getUserId(): string {
  let userId = localStorage.getItem("ai_user_id");
  if (!userId) {
    userId = "user_" + Math.random().toString(36).slice(2, 12);
    localStorage.setItem("ai_user_id", userId);
  }
  return userId;
}

export function getCurrentUserId(): string {
  return getUserId();
}

async function readErrorMessage(response: Response): Promise<string> {
  const body = await response.text().catch(() => "");
  if (!body) return `HTTP ${response.status}`;
  try {
    const data = JSON.parse(body) as { detail?: unknown; message?: unknown };
    if (typeof data.detail === "string") return data.detail;
    if (typeof data.message === "string") return data.message;
  } catch {
    // Non-JSON fallback is still useful for a gateway error.
  }
  return body;
}

async function request(path: string, init: RequestInit = {}): Promise<Response> {
  const response = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      "X-User-ID": getUserId(),
      ...(init.headers || {}),
    },
  });
  if (!response.ok) {
    throw new ApiError(response.status, await readErrorMessage(response));
  }
  return response;
}

export async function createThread(): Promise<string> {
  const response = await request("/api/v1/threads", { method: "POST" });
  const data = await response.json();
  return data.thread_id as string;
}

export async function listThreads(): Promise<ThreadSummary[]> {
  const response = await request("/api/v1/threads");
  const data = await response.json();
  return (data.threads ?? []) as ThreadSummary[];
}

export async function deleteThread(threadId: string): Promise<void> {
  await request(`/api/v1/threads/${threadId}`, { method: "DELETE" });
}

export async function getMessages(threadId: string): Promise<ChatHistoryMessage[]> {
  const response = await request(`/api/v1/threads/${threadId}/messages`);
  const data = await response.json();
  return (data.messages ?? []) as ChatHistoryMessage[];
}

export async function getThreadState(threadId: string): Promise<ThreadStateResponse> {
  const response = await request(`/threads/${threadId}`);
  return (await response.json()) as ThreadStateResponse;
}

export async function getInterruptToolDefinitions(): Promise<InterruptToolDefinition[]> {
  const response = await request("/api/v1/interrupt-tools");
  const data = await response.json();
  return (data.tools ?? []) as InterruptToolDefinition[];
}

export function artifactFileUrl(threadId: string, artifactId: string): string {
  return `${API_BASE}/api/v1/threads/${threadId}/artifacts/${artifactId}/file`;
}

interface SendMessageParams {
  threadId: string;
  text?: string;
  payload?: Record<string, unknown>;
  toolCallId?: string;
  displayText?: string;
  signal?: AbortSignal;
}

function parseSseBlock(rawEvent: string): SseEvent | null {
  let eventName = "message";
  const dataLines: string[] = [];

  for (const line of rawEvent.split("\n")) {
    if (line.startsWith("event:")) {
      eventName = line.slice(6).trim();
    } else if (line.startsWith("data:")) {
      // Preserve a space after `data:` if it was intentionally part of a JSON
      // string. SSE permits multiple data lines, which join with newline.
      dataLines.push(line.slice(5).replace(/^ /, ""));
    }
  }

  if (dataLines.length === 0) return null;
  try {
    return { event: eventName, data: JSON.parse(dataLines.join("\n")) } as SseEvent;
  } catch {
    return null;
  }
}

export async function* sendMessage(params: SendMessageParams): AsyncGenerator<SseEvent> {
  const body: Record<string, unknown> = { thread_id: params.threadId };
  if (params.text !== undefined) body.text = params.text;
  if (params.payload !== undefined) body.payload = params.payload;
  if (params.toolCallId !== undefined) body.tool_call_id = params.toolCallId;
  if (params.displayText !== undefined) body.display_text = params.displayText;

  const response = await fetch(`${API_BASE}/message`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "Accept": "text/event-stream",
      "X-User-ID": getUserId(),
    },
    body: JSON.stringify(body),
    signal: params.signal,
  });

  if (!response.ok || !response.body) {
    throw new ApiError(response.status, await readErrorMessage(response));
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  try {
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;

      buffer += decoder.decode(value, { stream: true }).replace(/\r\n|\r/g, "\n");
      let separatorIndex = buffer.indexOf("\n\n");
      while (separatorIndex !== -1) {
        const event = parseSseBlock(buffer.slice(0, separatorIndex));
        buffer = buffer.slice(separatorIndex + 2);
        if (event) yield event;
        separatorIndex = buffer.indexOf("\n\n");
      }
    }

    buffer += decoder.decode();
    if (buffer.trim()) {
      const event = parseSseBlock(buffer);
      if (event) yield event;
    }
  } finally {
    reader.releaseLock();
  }
}

export async function listMyPresentations(): Promise<PresentationRecord[]> {
  const response = await request("/api/v1/presentations/mine");
  const data = await response.json();
  return (data.presentations ?? []) as PresentationRecord[];
}

export async function listSharedPresentations(): Promise<PresentationRecord[]> {
  const response = await request("/api/v1/presentations/shared");
  const data = await response.json();
  return (data.presentations ?? []) as PresentationRecord[];
}

export async function getPresentationDetail(id: string): Promise<PresentationRecord> {
  const response = await request(`/api/v1/presentations/${id}`);
  return (await response.json()) as PresentationRecord;
}

export async function updatePresentationFields(id: string, fields: PresentationFields): Promise<void> {
  await request(`/api/v1/presentations/${id}`, {
    method: "PATCH",
    body: JSON.stringify({ fields }),
  });
}

export async function publishPresentation(id: string): Promise<void> {
  await request(`/api/v1/presentations/${id}/publish`, { method: "POST" });
}

export async function unpublishPresentation(id: string): Promise<void> {
  await request(`/api/v1/presentations/${id}/unpublish`, { method: "POST" });
}

export async function deletePresentationRecord(id: string): Promise<void> {
  await request(`/api/v1/presentations/${id}`, { method: "DELETE" });
}

export function presentationFileUrl(id: string): string {
  return `${API_BASE}/api/v1/presentations/${id}/file`;
}
