import type { Artifact } from "../types";
import { renderMarkdown, sanitizeHtml, highlightCodeBlocks } from "./markdown";
import { artifactFileUrl } from "../api";

export function getMessagesEl(): HTMLElement {
  return document.getElementById("messages")!;
}

export function clearEmptyState(): void {
  document.getElementById("emptyState")?.remove();
}

export function showEmptyState(icon: string, text: string, hint?: string): void {
  const messagesDiv = getMessagesEl();
  messagesDiv.innerHTML = `
    <div class="empty-state" id="emptyState">
      <div class="empty-state-icon">${icon}</div>
      <div class="empty-state-text">${text}</div>
      ${hint ? `<div class="empty-state-hint">${hint}</div>` : ""}
    </div>
  `;
}

function scrollToBottom(): void {
  const element = getMessagesEl();
  element.scrollTop = element.scrollHeight;
}

function appendDownloadLink(container: HTMLElement, threadId: string, artifact: Artifact): void {
  const version = artifact.versions[artifact.current_version];
  if (!version?.sections?.html) return;

  const link = document.createElement("a");
  link.className = "artifact-download-link";
  link.href = artifactFileUrl(threadId, artifact.id);
  link.textContent = "⬇ Скачать файл";
  link.setAttribute("download", "");
  container.appendChild(link);
}

export function addMessage(
  threadId: string,
  role: "user" | "assistant",
  content: string,
  artifact?: Artifact | null,
): void {
  clearEmptyState();
  const messagesDiv = getMessagesEl();

  const message = document.createElement("div");
  message.className = `message ${role}`;

  const contentDiv = document.createElement("div");
  contentDiv.className = "message-content markdown-body";

  if (role === "assistant") {
    if (artifact) {
      const html = artifact.versions[artifact.current_version]?.sections?.html;
      if (typeof html === "string") {
        contentDiv.innerHTML = sanitizeHtml(html);
      } else {
        contentDiv.innerHTML = renderMarkdown(content);
        highlightCodeBlocks(contentDiv);
      }
      appendDownloadLink(contentDiv, threadId, artifact);
    } else {
      contentDiv.innerHTML = renderMarkdown(content);
      highlightCodeBlocks(contentDiv);
    }
  } else {
    contentDiv.textContent = content;
  }

  message.appendChild(contentDiv);
  messagesDiv.appendChild(message);
  scrollToBottom();
}

export function addErrorMessage(text: string): void {
  clearEmptyState();
  const messagesDiv = getMessagesEl();
  const message = document.createElement("div");
  message.className = "message assistant";

  const content = document.createElement("div");
  content.className = "message-content markdown-body error-message";
  content.textContent = `❌ ${text}`;

  message.appendChild(content);
  messagesDiv.appendChild(message);
  scrollToBottom();
}

let typingEl: HTMLElement | null = null;
let typingTextEl: HTMLElement | null = null;

/**
 * Shows ephemeral user-safe agent activity. Repeated calls reuse the same DOM
 * node instead of removing/recreating it, which keeps frequent reasoning
 * events cheap. `main.ts` batches calls through requestAnimationFrame.
 */
export function showTyping(text = "Агент обрабатывает запрос…"): void {
  clearEmptyState();
  const messagesDiv = getMessagesEl();

  if (!typingEl) {
    typingEl = document.createElement("div");
    typingEl.className = "message assistant agent-activity";

    const indicator = document.createElement("div");
    indicator.className = "typing-indicator";
    indicator.setAttribute("role", "status");
    indicator.setAttribute("aria-live", "polite");

    const dots = document.createElement("span");
    dots.className = "typing-dots";
    dots.setAttribute("aria-hidden", "true");
    dots.innerHTML = "<span></span><span></span><span></span>";

    typingTextEl = document.createElement("span");
    typingTextEl.className = "typing-text";

    indicator.append(dots, typingTextEl);
    typingEl.appendChild(indicator);
    messagesDiv.appendChild(typingEl);
  }

  if (typingTextEl) typingTextEl.textContent = text;
  scrollToBottom();
}

export function hideTyping(): void {
  typingEl?.remove();
  typingEl = null;
  typingTextEl = null;
}
