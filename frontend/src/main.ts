import "./styles.css";
import {
  ApiError,
  createThread,
  getCurrentUserId,
  getMessages,
  getThreadState,
  sendMessage,
} from "./api";
import type { SseEvent, ToolCall } from "./types";
import {
  addErrorMessage,
  addMessage,
  getMessagesEl,
  hideTyping,
  showEmptyState,
  showTyping,
} from "./render/chat";
import {
  renderInterruptInto,
  type InterruptAnswer,
} from "./render/interactive";
import { renderThreadList, highlightThread, markThreadTyping } from "./render/sidebar";
import { renderGallery, type GalleryKind } from "./render/gallery";

let threadId: string | null = localStorage.getItem("ai_thread_id");
let processingThreadId: string | null = null;
const pendingControllers = new Map<string, AbortController>();
const drafts = new Map<string, string>();

let pendingInterrupt: ToolCall | null = null;
let pendingInterruptThreadId: string | null = null;
let pendingInterruptDraft: Record<string, unknown> = {};
let isSubmittingInterrupt = false;
let interruptError: string | null = null;

type View = "chat" | "mine" | "shared";
let currentView: View = "chat";

const els = {
  input: document.getElementById("messageInput") as HTMLTextAreaElement,
  sendBtn: document.getElementById("sendBtn") as HTMLButtonElement,
  interactiveSlot: document.getElementById("interactiveInputSlot") as HTMLElement,
  threadIdText: document.getElementById("threadIdText")!,
  chatView: document.getElementById("chatView") as HTMLElement,
  galleryView: document.getElementById("galleryView") as HTMLElement,
};

interface SubmitOptions {
  text?: string;
  payload?: Record<string, unknown>;
  toolCallId?: string;
  displayText?: string;
}

function updateThreadLabel(): void {
  els.threadIdText.textContent = threadId ? `thread: ${threadId}` : "—";
}

function hasActiveInterrupt(): boolean {
  return pendingInterrupt !== null && pendingInterruptThreadId === threadId;
}

function setPendingInterrupt(targetThreadId: string, toolCall: ToolCall): void {
  const changed = pendingInterruptThreadId !== targetThreadId || pendingInterrupt?.id !== toolCall.id;
  pendingInterrupt = toolCall;
  pendingInterruptThreadId = targetThreadId;
  if (changed) {
    pendingInterruptDraft = {};
    interruptError = null;
  }
  isSubmittingInterrupt = false;
  renderComposer();
}

function clearPendingInterrupt(): void {
  pendingInterrupt = null;
  pendingInterruptThreadId = null;
  pendingInterruptDraft = {};
  isSubmittingInterrupt = false;
  interruptError = null;
  renderComposer();
}

function renderComposer(): void {
  const active = hasActiveInterrupt();
  els.input.hidden = active;
  els.sendBtn.hidden = active;
  els.interactiveSlot.hidden = !active;

  if (!active || !pendingInterrupt) {
    els.interactiveSlot.replaceChildren();
    return;
  }

  renderInterruptInto(els.interactiveSlot, {
    toolCall: pendingInterrupt,
    disabled: isSubmittingInterrupt,
    initialValues: pendingInterruptDraft,
    errorMessage: interruptError,
    onValuesChange: (values) => {
      pendingInterruptDraft = values;
      renderComposer();
    },
    onSubmit: (answer, toolCallId) => {
      void submitInterruptAnswer(answer, toolCallId);
    },
  });
}

function updateSendButton(): void {
  if (hasActiveInterrupt()) return;
  const isProcessingCurrent = processingThreadId !== null && processingThreadId === threadId;
  const hasPending = threadId !== null && pendingControllers.has(threadId);
  if (isProcessingCurrent || hasPending) {
    els.sendBtn.textContent = "✕ Отмена";
    els.sendBtn.className = "cancel-btn";
    els.sendBtn.onclick = () => cancelCurrentRequest(processingThreadId ?? threadId!);
  } else {
    els.sendBtn.textContent = "📤 Отправить";
    els.sendBtn.className = "";
    els.sendBtn.onclick = () => void handleSendClick();
  }
}

function cancelCurrentRequest(targetThreadId: string): void {
  pendingControllers.get(targetThreadId)?.abort();
  pendingControllers.delete(targetThreadId);
  markThreadTyping(targetThreadId, false);
  processingThreadId = null;
  if (targetThreadId === threadId) {
    hideTyping();
    updateSendButton();
  }
}

function switchView(view: View): void {
  currentView = view;
  document.querySelectorAll<HTMLElement>(".view-tab").forEach((btn) => {
    btn.classList.toggle("active", btn.dataset.view === view);
  });

  if (view === "chat") {
    els.chatView.style.display = "";
    els.galleryView.style.display = "none";
  } else {
    els.chatView.style.display = "none";
    els.galleryView.style.display = "";
    void renderGallery(els.galleryView, view as GalleryKind, { currentUserId: getCurrentUserId() });
  }
}

async function submitToServer(targetThreadId: string, opts: SubmitOptions): Promise<void> {
  if (pendingControllers.has(targetThreadId)) {
    throw new Error("Запрос для этого треда уже выполняется");
  }

  const controller = new AbortController();
  pendingControllers.set(targetThreadId, controller);
  processingThreadId = targetThreadId;
  markThreadTyping(targetThreadId, true);
  if (targetThreadId === threadId) {
    showTyping();
    updateSendButton();
  }

  try {
    for await (const event of sendMessage({
      threadId: targetThreadId,
      text: opts.text,
      payload: opts.payload,
      toolCallId: opts.toolCallId,
      displayText: opts.displayText,
      signal: controller.signal,
    })) {
      handleSseEvent(targetThreadId, event);
    }
  } finally {
    pendingControllers.delete(targetThreadId);
    markThreadTyping(targetThreadId, false);
    if (processingThreadId === targetThreadId) processingThreadId = null;
    if (targetThreadId === threadId) updateSendButton();
    void renderSidebar();
  }
}

function handleSseEvent(targetThreadId: string, event: SseEvent): void {
  if (event.event === "error") {
    if (targetThreadId === threadId) {
      hideTyping();
      addErrorMessage(event.data.message);
    }
    return;
  }
  if (event.event !== "message") return;

  const data = event.data;
  if (targetThreadId === threadId) hideTyping();

  if (data.awaiting_input && data.tool_calls?.length) {
    if (targetThreadId === threadId) setPendingInterrupt(targetThreadId, data.tool_calls[0]);
    return;
  }

  if (targetThreadId === threadId) clearPendingInterrupt();
  if (data.content) addMessage(targetThreadId, "assistant", data.content, data.artifact);
}

async function submitInterruptAnswer(answer: InterruptAnswer, toolCallId: string): Promise<void> {
  if (
    !threadId ||
    !pendingInterrupt ||
    pendingInterruptThreadId !== threadId ||
    pendingInterrupt.id !== toolCallId ||
    isSubmittingInterrupt
  ) {
    return;
  }

  const targetThreadId = threadId;
  isSubmittingInterrupt = true;
  interruptError = null;
  renderComposer();

  try {
    await submitToServer(targetThreadId, {
      text: answer.kind === "text" ? answer.text : undefined,
      payload: answer.kind === "payload" ? answer.payload : undefined,
      toolCallId,
      displayText: answer.kind === "text"
        ? (answer.displayText ?? answer.text)
        : answer.displayText,
    });
  } catch (error) {
    if (targetThreadId !== threadId || (error as Error).name === "AbortError") return;
    hideTyping();
    isSubmittingInterrupt = false;
    interruptError = error instanceof ApiError ? error.message : "Не удалось отправить ответ. Попробуйте ещё раз.";
    renderComposer();
  }
}

async function renderSidebar(): Promise<void> {
  await renderThreadList(threadId, {
    onSelect: (id) => void switchThread(id),
    onDelete: (id) => { if (threadId === id) newChat(); else void renderSidebar(); },
  });
}

async function switchThread(newThreadId: string): Promise<void> {
  if (currentView !== "chat") switchView("chat");
  if (newThreadId === threadId) return;

  clearPendingInterrupt();
  threadId = newThreadId;
  localStorage.setItem("ai_thread_id", threadId);
  updateThreadLabel();
  highlightThread(threadId);
  restoreDraft();
  updateSendButton();
  showEmptyState("⏳", "загружаем диалог...");
  await loadMessages();
}

async function loadMessages(): Promise<void> {
  const targetThreadId = threadId;
  if (!targetThreadId) return;

  try {
    const [messages, state] = await Promise.all([getMessages(targetThreadId), getThreadState(targetThreadId)]);
    if (threadId !== targetThreadId) return;

    getMessagesEl().innerHTML = "";
    if (messages.length === 0) {
      showEmptyState("💬", "в этом чате пока нет сообщений", "начните диалог, задав вопрос об инциденте");
    } else {
      for (const message of messages) addMessage(targetThreadId, message.role, message.content, message.artifact);
    }

    if (state.awaiting_input && state.tool_calls?.length) {
      setPendingInterrupt(targetThreadId, state.tool_calls[0]);
    } else {
      clearPendingInterrupt();
    }
    if (pendingControllers.has(targetThreadId)) showTyping();
  } catch (error) {
    if (threadId !== targetThreadId) return;
    console.error("loadMessages error:", error);
    clearPendingInterrupt();
    showEmptyState("❌", "Ошибка загрузки");
  }
}

function newChat(): void {
  if (currentView !== "chat") switchView("chat");
  if (threadId) cancelCurrentRequest(threadId);
  threadId = null;
  localStorage.removeItem("ai_thread_id");
  clearPendingInterrupt();
  showEmptyState("💬", "Начните новый диалог", "Задайте вопрос об инциденте или опишите проблему");
  updateThreadLabel();
  highlightThread(null);
  els.input.value = "";
  autoResize(els.input);
  updateSendButton();
  void renderSidebar();
}

let isCreatingThread = false;
async function handleSendClick(): Promise<void> {
  if (hasActiveInterrupt()) return;

  if (!threadId) {
    if (isCreatingThread) return;
    isCreatingThread = true;
    try {
      threadId = await createThread();
      localStorage.setItem("ai_thread_id", threadId);
      updateThreadLabel();
    } catch {
      addErrorMessage("Не удалось создать сессию. Попробуйте обновить страницу.");
      return;
    } finally {
      isCreatingThread = false;
    }
  }

  const targetThreadId = threadId;
  const text = els.input.value.trim();
  if (!targetThreadId || !text || pendingControllers.has(targetThreadId)) return;

  drafts.set(targetThreadId, "");
  els.input.value = "";
  autoResize(els.input);
  addMessage(targetThreadId, "user", text);

  try {
    await submitToServer(targetThreadId, { text });
  } catch (error) {
    if ((error as Error).name !== "AbortError" && targetThreadId === threadId) {
      hideTyping();
      addErrorMessage(error instanceof ApiError ? error.message : String(error));
    }
  }
}

function saveDraft(): void {
  if (threadId) drafts.set(threadId, els.input.value);
}

function restoreDraft(): void {
  els.input.value = (threadId && drafts.get(threadId)) || "";
  autoResize(els.input);
}

function autoResize(element: HTMLTextAreaElement): void {
  element.style.height = "auto";
  const minHeight = parseInt(getComputedStyle(element).minHeight) || 60;
  const maxHeight = parseInt(getComputedStyle(element).maxHeight) || 200;
  element.style.height = `${Math.max(Math.min(element.scrollHeight, maxHeight), minHeight)}px`;
}

document.addEventListener("DOMContentLoaded", () => {
  if (typeof marked !== "undefined") marked.setOptions({ breaks: true, gfm: true });

  els.input.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      void handleSendClick();
    }
  });
  els.input.addEventListener("input", () => {
    autoResize(els.input);
    saveDraft();
  });
  document.getElementById("newChatBtn")!.addEventListener("click", newChat);
  document.querySelectorAll<HTMLElement>(".view-tab").forEach((button) => {
    button.addEventListener("click", () => switchView(button.dataset.view as View));
  });
  window.addEventListener("open-my-presentations", () => switchView("mine"));

  updateThreadLabel();
  renderComposer();
  updateSendButton();
  if (threadId) void loadMessages();
  void renderSidebar();
});
