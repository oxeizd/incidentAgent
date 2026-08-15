/**
 * Типы API-контракта, зеркалящие backend (app/api/schemas.py, app/api/sse.py,
 * app/api/app.py, app/ai/runtime/form_schema.py).
 */

export interface FunctionCall {
  name: string;
  arguments: string;
}

export interface ToolCall {
  id: string;
  type: "function";
  function: FunctionCall;
}

export interface InteractiveOption {
  label: string;
  value?: string;
  allowCustom?: boolean;
}

export interface AskUserArgs {
  question: string;
  options?: InteractiveOption[];
  submitLabel?: string;
}

export interface AskConfirmationArgs {
  question: string;
  options?: InteractiveOption[];
  submitLabel?: string;
}

export interface FormField {
  name: string;
  label: string;
  required: boolean;
  type:
    | "string"
    | "textarea"
    | "boolean"
    | "integer"
    | "number"
    | "array"
    | "object"
    | "select";
  value?: unknown;
  placeholder?: string;
  options?: string[];
  items?: { type: string };
}

export interface AskFormArgs {
  question: string;
  fields: FormField[];
  submitLabel?: string;
}

export interface InterruptToolDefinition {
  type: "function";
  function: {
    name: "ask_user" | "ask_confirmation" | "ask_form";
    description: string;
    parameters: Record<string, unknown>;
  };
}

export interface ArtifactVersion {
  version: number;
  sections: Record<string, unknown> & { html?: string };
  produced_by_worker_id: string;
  note: string;
  timestamp: string;
}

export interface Artifact {
  id: string;
  kind: string;
  status: string;
  versions: ArtifactVersion[];
  current_version: number;
  created_by_worker_id: string;
  created_at: string;
}

export function artifactHtml(artifact: Artifact | null | undefined): string | null {
  if (!artifact) return null;
  const version = artifact.versions[artifact.current_version];
  return typeof version?.sections?.html === "string" ? version.sections.html : null;
}

export interface ThreadSummary {
  id: string;
  created_at?: string;
}

export interface ThreadStateResponse {
  thread_id: string;
  awaiting_input: boolean;
  question: string | null;
  pending_artifact: Artifact | null;
  next_nodes: string[];
  current_artifact_id: string | null;
  tool_calls: ToolCall[] | null;
}

export interface ChatHistoryMessage {
  role: "user" | "assistant";
  content: string;
  artifact?: Artifact | null;
}

export interface SseMessagePayload {
  content: string;
  artifact: Artifact | null;
  awaiting_input: boolean;
  tool_calls: ToolCall[] | null;
}

export interface SseErrorPayload {
  message: string;
}

export type SseEvent =
  | { event: "message"; data: SseMessagePayload }
  | { event: "error"; data: SseErrorPayload }
  | { event: "reasoning"; data: unknown }
  | { event: "done"; data: { ok: boolean } };

export interface PresentationFields {
  number: string;
  unit: string;
  team: string;
  brief: string;
  description: string;
  cause: string;
  chain: string;
  stage: string;
  impact: string;
  losses: string;
  operational_measures: string;
  systemic_measures: string;
  timeline: string[];
}

export type PresentationStatus = "draft" | "published";

export interface PresentationRecord {
  id: string;
  owner_user_id: string;
  thread_id: string;
  status: PresentationStatus;
  fields: PresentationFields;
  analysis_markdown: string | null;
  published_snapshot: PresentationFields | null;
  created_at: string;
  updated_at: string;
  published_at: string | null;
}
