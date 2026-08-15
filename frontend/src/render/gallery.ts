import type { PresentationRecord } from "../types";
import { listMyPresentations, listSharedPresentations } from "../api";
import { renderPresentationEditor } from "./presentation";

export type GalleryKind = "mine" | "shared";

interface GalleryOptions {
  currentUserId: string;
}

/**
 * Список карточек презентаций ("Мои презентации" — draft+published текущего
 * владельца, "Общее хранилище" — published от всех).
 *
 * ИСПРАВЛЕНО: раньше при открытии своей презентации из "Общего хранилища"
 * isOwner вычислялся по owner_user_id — UI показывал полноценный редактор с
 * кнопками "Снять с публикации" и особенно опасной "Удалить". Технически
 * delete удалял единственную запись presentations (и она закономерно
 * исчезала также из "Моих презентаций" — это один объект, не две копии),
 * но UX создавал ложное ожидание "удаляю только общую публикацию". теперь
 * shared-view ВСЕГДА read-only, в том числе для владельца; владельцу вместо
 * мутирующих кнопок показывается явная кнопка "Открыть в моих презентациях".
 * все операции изменения (save/publish/unpublish/delete) физически доступны
 * только внутри GalleryKind="mine".
 */
export async function renderGallery(
  container: HTMLElement, kind: GalleryKind, opts: GalleryOptions,
): Promise<void> {
  container.innerHTML = '<div class="gallery-loading">загрузка...</div>';

  let presentations: PresentationRecord[];
  try {
    presentations = kind === "mine" ? await listMyPresentations() : await listSharedPresentations();
  } catch (e) {
    container.innerHTML = `<div class="gallery-error">не удалось загрузить: ${e}</div>`;
    return;
  }

  if (presentations.length === 0) {
    container.innerHTML = `<div class="empty-state">
      <div class="empty-state-icon">🗂️</div>
      <div class="empty-state-text">${kind === "mine" ? "пока нет ни одной презентации" : "общее хранилище пусто"}</div>
      <div class="empty-state-hint">${kind === "mine" ? "создайте презентацию в чате — она появится здесь автоматически" : "опубликуйте презентацию из «Моих презентаций»"}</div>
    </div>`;
    return;
  }

  const grid = document.createElement("div");
  grid.className = "presentation-grid";

  for (const p of presentations) {
    grid.appendChild(renderCard(p, kind, opts, () => {
      void renderGallery(container, kind, opts);
    }));
  }

  container.innerHTML = "";
  container.appendChild(grid);
}

function renderCard(
  presentation: PresentationRecord, kind: GalleryKind, opts: GalleryOptions, onBack: () => void,
): HTMLElement {
  const card = document.createElement("div");
  card.className = "presentation-card";

  const badge = document.createElement("span");
  badge.className = `presentation-status-badge status-${presentation.status}`;
  badge.textContent = presentation.status === "published" ? "Опубликована" : "черновик";
  card.appendChild(badge);

  const title = document.createElement("div");
  title.className = "presentation-card-title";
  // В shared-каталоге всегда показываем frozen snapshot, в mine — рабочие
  // fields (даже если presentation уже опубликована и пользователь после
  // публикации начал править черновик).
  const fields = kind === "shared" ? presentation.published_snapshot! : presentation.fields;
  title.textContent = `${fields.number} — ${fields.brief}`;
  card.appendChild(title);

  const meta = document.createElement("div");
  meta.className = "presentation-card-meta";
  meta.textContent = `обновлено: ${new Date(presentation.updated_at).toLocaleString("ru-RU")}`;
  card.appendChild(meta);

  card.addEventListener("click", () => {
    const parent = card.closest(".gallery-view") as HTMLElement;
    if (!parent) return;
    parent.innerHTML = "";

    // ВАЖНО: kind === "mine", а не owner_user_id === currentUserId. общий
    // каталог — неизменно read-only для всех; так пользователь никогда не
    // удалит личную запись из контекста "общей публикации".
    const canEdit = kind === "mine" && presentation.owner_user_id === opts.currentUserId;
    parent.appendChild(
      renderPresentationEditor(presentation, {
        isOwner: canEdit,
        source: kind,
        onClose: onBack,
        onChanged: onBack,
        onOpenMine: () => {
          // смена view управляется main.ts через кастомное событие: gallery
          // не импортирует main.ts (иначе была бы циклическая зависимость).
          window.dispatchEvent(new CustomEvent("open-my-presentations"));
        },
      }),
    );
  });

  return card;
}
