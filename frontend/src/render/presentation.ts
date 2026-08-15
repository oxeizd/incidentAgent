import type { PresentationFields, PresentationRecord } from "../types";
import {
  deletePresentationRecord,
  presentationFileUrl,
  publishPresentation,
  unpublishPresentation,
  updatePresentationFields,
} from "../api";
import { collectFieldsFromSlide, renderVisualPresentation } from "./visual-presentation";
import "./visual-presentation.css";

export type PresentationSource = "mine" | "shared";

export interface PresentationEditorOptions {
  isOwner: boolean;
  source: PresentationSource;
  onClose: () => void;
  onChanged: () => void;
  onOpenMine?: () => void;
}

function emptyFields(): PresentationFields {
  return {
    number: "—",
    unit: "—",
    team: "—",
    brief: "—",
    description: "—",
    cause: "—",
    chain: "—",
    stage: "—",
    impact: "—",
    losses: "—",
    operational_measures: "—",
    systemic_measures: "—",
    timeline: [],
  };
}

function fieldsForView(record: PresentationRecord, source: PresentationSource): PresentationFields {
  if (source === "shared") {
    return record.published_snapshot ?? emptyFields();
  }
  return record.fields ?? emptyFields();
}

export function renderPresentationEditor(
  record: PresentationRecord,
  opts: PresentationEditorOptions,
): HTMLElement {
  const wrap = document.createElement("div");
  wrap.className = "presentation-editor";

  const toolbar = document.createElement("div");
  toolbar.className = "presentation-toolbar";

  const back = button("← Назад", () => opts.onClose());
  toolbar.appendChild(back);

  const status = document.createElement("span");
  status.className = `presentation-status-badge status-${record.status}`;
  status.textContent = record.status === "published" ? "Опубликована" : "черновик";
  toolbar.appendChild(status);

  const slideHost = document.createElement("div");
  slideHost.className = "presentation-slide-host";

  let editing = false;
  const working: PresentationFields = { ...fieldsForView(record, opts.source) };

  const paint = () => {
    slideHost.innerHTML = "";
    slideHost.appendChild(
      renderVisualPresentation(working, {
        editable: opts.source === "mine" && opts.isOwner,
        editing: editing && opts.source === "mine" && opts.isOwner,
      }),
    );
  };

  if (opts.source === "shared") {
    if (opts.isOwner || record.owner_user_id) {
      toolbar.appendChild(
        button("Открыть в моих презентациях", () => opts.onOpenMine?.()),
      );
    }
    toolbar.appendChild(downloadLink(record.id, "published", "Скачать опубликованную версию"));
  } else {
    const editBtn = button("Редактировать", () => {
      if (editing) {
        Object.assign(working, collectFieldsFromSlide(slideHost, working));
      }
      editing = !editing;
      editBtn.textContent = editing ? "Завершить правку" : "Редактировать";
      paint();
    });
    toolbar.appendChild(editBtn);

    toolbar.appendChild(
      button("Сохранить изменения", async () => {
        const next = collectFieldsFromSlide(slideHost, working);
        Object.assign(working, next);
        await updatePresentationFields(record.id, next);
        opts.onChanged();
      }),
    );

    toolbar.appendChild(
      button(record.status === "published" ? "Обновить публикацию" : "Опубликовать", async () => {
        const next = collectFieldsFromSlide(slideHost, working);
        await updatePresentationFields(record.id, next);
        await publishPresentation(record.id);
        opts.onChanged();
      }),
    );

    if (record.status === "published") {
      toolbar.appendChild(
        button("Снять с публикации", async () => {
          await unpublishPresentation(record.id);
          opts.onChanged();
        }),
      );
    }

    toolbar.appendChild(
      button("Удалить", async () => {
        if (!confirm("Удалить презентацию? Запись исчезнет и из общего хранилища.")) return;
        await deletePresentationRecord(record.id);
        opts.onChanged();
      }),
    );

    toolbar.appendChild(downloadLink(record.id, "draft", "Скачать draft-версию"));
  }

  wrap.appendChild(toolbar);
  wrap.appendChild(slideHost);
  paint();
  return wrap;
}

function button(label: string, onClick: () => void | Promise<void>): HTMLButtonElement {
  const btn = document.createElement("button");
  btn.type = "button";
  btn.className = "presentation-tool-btn";
  btn.textContent = label;
  btn.addEventListener("click", () => {
    void onClick();
  });
  return btn;
}

function downloadLink(id: string, version: "draft" | "published", label: string): HTMLAnchorElement {
  const a = document.createElement("a");
  a.className = "presentation-tool-btn";
  a.href = `${presentationFileUrl(id)}?version=${version}`;
  a.textContent = label;
  a.setAttribute("download", `${id}.html`);
  return a;
}
