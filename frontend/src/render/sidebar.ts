import type { ThreadSummary } from "../types";
import { listThreads, deleteThread } from "../api";

interface SidebarCallbacks {
  onSelect: (threadId: string) => void;
  onDelete: (threadId: string) => void;
}

const typingThreads = new Set<string>();

export function markThreadTyping(threadId: string, isTyping: boolean): void {
  if (isTyping) typingThreads.add(threadId);
  else typingThreads.delete(threadId);
  const item = document.querySelector(`.sidebar-item[data-thread-id="${threadId}"]`);
  if (!item) return;
  const existingDot = item.querySelector(".typing-dot");
  if (isTyping && !existingDot) {
    const dot = document.createElement("span");
    dot.className = "typing-dot";
    dot.textContent = "●";
    const delBtn = item.querySelector(".sidebar-del-btn");
    delBtn ? item.insertBefore(dot, delBtn) : item.appendChild(dot);
  } else if (!isTyping && existingDot) {
    existingDot.remove();
  }
}

export async function renderThreadList(activeThreadId: string | null, cb: SidebarCallbacks): Promise<void> {
  const list = document.getElementById("threadList")!;
  let threads: ThreadSummary[];
  try {
    threads = await listThreads();
  } catch (e) {
    console.error("renderThreadList error:", e);
    return;
  }

  list.innerHTML = "";
  if (threads.length === 0) {
    list.innerHTML = '<div class="sidebar-empty">Нет чатов</div>';
    return;
  }

  const fragment = document.createDocumentFragment();
  for (const t of threads) {
    const item = document.createElement("div");
    item.className = "sidebar-item" + (t.id === activeThreadId ? " active" : "");
    item.dataset.threadId = t.id;
    item.title = t.created_at ?? "";

    const icon = document.createElement("span");
    icon.className = "thread-icon";
    icon.textContent = "💬";
    item.appendChild(icon);

    const nameSpan = document.createElement("span");
    nameSpan.className = "sidebar-item-name";
    nameSpan.textContent = t.id;
    item.appendChild(nameSpan);

    if (typingThreads.has(t.id)) {
      const dot = document.createElement("span");
      dot.className = "typing-dot";
      dot.textContent = "●";
      item.appendChild(dot);
    }

    const delBtn = document.createElement("button");
    delBtn.className = "sidebar-del-btn";
    delBtn.textContent = "✕";
    delBtn.title = "удалить диалог";
    delBtn.addEventListener("click", async (e) => {
      e.stopPropagation();
      if (!confirm("удалить этот диалог?")) return;
      try {
        await deleteThread(t.id);
        cb.onDelete(t.id);
      } catch (err) {
        console.error("delete error:", err);
      }
    });
    item.appendChild(delBtn);

    item.addEventListener("click", (e) => {
      if ((e.target as HTMLElement).closest(".sidebar-del-btn")) return;
      cb.onSelect(t.id);
    });

    fragment.appendChild(item);
  }
  list.appendChild(fragment);
}

export function highlightThread(activeThreadId: string | null): void {
  document.querySelectorAll(".sidebar-item").forEach((el) => {
    el.classList.toggle("active", (el as HTMLElement).dataset.threadId === activeThreadId);
  });
}
