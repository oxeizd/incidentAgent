/**
 * Public client contracts.
 *
 * Mirrors app/api/schemas.py and app/api/sse.py. The SSE API emits canonical
 * versioned events first, while `message`/`reasoning` stay supported as
 * migration aliases for older clients.
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

export type AgentEventType =
  | "run.started"
  | "agent.status"
  | "agent.reasoning"
  | "tool.started"
  | "tool.completed"
  | "tool.failed"
  | "interrupt.requested"
  | "message.delta"
  | "message.final"
  | "artifact.created"
  | "run.completed"
  | "run.failed";

export interface AgentEventEnvelope<T = Record<string, unknown>> {
  schema_version: 1;
  event_id: string;
  thread_id: string;
  run_id: string;
  sequence: number;
  timestamp: string;
  type: AgentEventType;
  visibility: "user" | "developer";
  worker_id: string | null;
  node: string | null;
  stage: string | null;
  data: T;
}

export interface AgentProgressData {
  message: string;
}

export interface AgentFinalData extends SseMessagePayload {}

export interface AgentFailureData {
  code: string;
  message: string;
  retryable: boolean;
  retry_after_ms: number | null;
}

export type SseEvent =
  | { event: "run.started"; data: AgentEventEnvelope }
  | { event: "agent.status"; data: AgentEventEnvelope<AgentProgressData> }
  | { event: "agent.reasoning"; data: AgentEventEnvelope<AgentProgressData> }
  | { event: "tool.started"; data: AgentEventEnvelope }
  | { event: "tool.completed"; data: AgentEventEnvelope }
  | { event: "tool.failed"; data: AgentEventEnvelope<AgentFailureData> }
  | { event: "interrupt.requested"; data: AgentEventEnvelope }
  | { event: "message.delta"; data: AgentEventEnvelope<{ delta: string }> }
  | { event: "message.final"; data: AgentEventEnvelope<AgentFinalData> }
  | { event: "artifact.created"; data: AgentEventEnvelope }
  | { event: "run.completed"; data: AgentEventEnvelope }
  | { event: "run.failed"; data: AgentEventEnvelope<AgentFailureData> }
  // Compatibility aliases retained during the backend rollout.
  | { event: "message"; data: SseMessagePayload }
  | { event: "reasoning"; data: AgentEventEnvelope<AgentProgressData> }
  | { event: "error"; data: AgentFailureData }
  | { event: "done"; data: { ok: boolean; run_id?: string } };

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
