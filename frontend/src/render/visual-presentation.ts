import type { PresentationFields } from "../types";

const DASH = "—";
const MONTHS: Record<string, string> = {
  "01": "янв", "02": "фев", "03": "мар", "04": "апр",
  "05": "мая", "06": "июн", "07": "июл", "08": "авг",
  "09": "сен", "10": "окт", "11": "ноя", "12": "дек",
};

export interface VisualPresentationOptions {
  editable: boolean;
  editing: boolean;
}

function esc(text: unknown): string {
  if (text === null || text === undefined || text === "") return DASH;
  return String(text)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/\n/g, "<br>");
}

function textOf(value: unknown): string {
  if (value === null || value === undefined) return DASH;
  const s = String(value).trim();
  return s || DASH;
}

function extractTeamAndPerson(teamRaw: string): { team: string; person: string } {
  if (!teamRaw || teamRaw === DASH) return { team: DASH, person: DASH };
  const parts = teamRaw.split(",").map((p) => p.trim()).filter(Boolean);
  return { team: parts[0] ?? DASH, person: parts[1] ?? DASH };
}

function extractPersons(fullTeam: string): string {
  if (!fullTeam || !fullTeam.includes(",")) return textOf(fullTeam);
  const parts = fullTeam.split(",").slice(1).map((p) => p.trim()).filter(Boolean);
  const fios = parts.filter((p) => p.split(/\s+/).length >= 2);
  return (fios.length ? fios : parts).join(", ") || DASH;
}

function extractCoreSystem(chain: string): string {
  if (!chain || chain === DASH) return DASH;
  const m = chain.match(/[Кк]орневой\s*:\s*(.+?)(?:Следствие:|$)/s);
  if (m) {
    const core = m[1].replace(/INC\d+\s*/gi, "").replace(/[-–>;]/g, " ").trim();
    return core || DASH;
  }
  return chain.slice(0, 120).trim() || DASH;
}

function formatLossesDisplay(lossText: string): string {
  if (!lossText || lossText === DASH) return "На оценке";
  const low = lossText.toLowerCase();
  if (["на оценке", "нет данных", "не оценен", "unknown"].some((k) => low.includes(k))) {
    return "На оценке";
  }
  const m = lossText.match(/(\d[\d\s,.]*)/);
  return m ? `${m[1].trim()} ₽` : lossText.trim();
}

function normalizeChainLines(chainText: string): string[] {
  if (!chainText.trim()) return [];
  let prepared = chainText.replace(/\s*(Корневой\s*:)/gi, "\n$1").replace(/\s*(Следствие\s*:)/gi, "\n$1");
  const raw = prepared.split("\n").map((x) => x.replace(/^[ ;]+|[ ;]+$/g, "")).filter(Boolean);
  const lines: string[] = [];
  let seenRoot = false;
  for (let line of raw) {
    line = line.replace(/^\d+\.\s*/, "").trim();
    if (/^корневой\s*:/i.test(line)) {
      const rest = line.replace(/^корневой\s*:\s*/i, "").trim();
      if (rest) {
        lines.push(`Корневой: ${rest}`);
        seenRoot = true;
      }
    } else if (/^следствие\s*:/i.test(line)) {
      const rest = line.replace(/^следствие\s*:\s*/i, "").trim();
      if (rest) lines.push(`Следствие: ${rest}`);
    } else {
      const prefix = !seenRoot && lines.length === 0 ? "Корневой" : "Следствие";
      lines.push(`${prefix}: ${line}`);
      if (prefix === "Корневой") seenRoot = true;
    }
  }
  return lines;
}

function parseMeasures(text: string): Array<{ measure: string; responsible: string }> {
  if (!text.trim()) return [];
  return text.split("\n").map((l) => l.trim()).filter(Boolean).map((line) => {
    const cleaned = line.replace(/^(\d+\.\s*|[•\-–]\s*)/, "").trim();
    const m = cleaned.match(/(,?\s*)(?:Отв(?:етственный)?\.?\s*:?\s*)/i);
    if (!m || m.index === undefined) return { measure: cleaned, responsible: DASH };
    const measure = cleaned.slice(0, m.index).replace(/[ ;.\-]+$/, "") || DASH;
    const rest = cleaned.slice(m.index).replace(/^[,;\s]+/, "");
    const fio = rest.match(/([А-ЯЁ][а-яё]+(?:\s+[А-ЯЁ][а-яё]+){1,2}|[А-ЯЁ][а-яё]+(?:\s+[А-ЯЁ]\.){1,2})/);
    const date = rest.match(/(\d{2}\.\d{2}\.\d{4}|\d{2}\.\d{2})/);
    const person = fio?.[1] ?? DASH;
    const deadline = date ? `до ${date[1]}` : DASH;
    const responsible = person !== DASH && deadline !== DASH ? `${person}, ${deadline}` : person !== DASH ? person : deadline;
    return { measure, responsible };
  });
}

function parseTimeline(events: string[]): Array<{ meta: string; desc: string; raw: string }> {
  const year = String(new Date().getFullYear());
  return events.map((line) => {
    const raw = line.trim();
    if (!raw) return { meta: "", desc: "", raw: "" };
    let m = raw.match(/^(\d{2}[./]\d{2}[./]\d{2,4})\s+(\d{1,2}[.:]\d{2})\s*[–\-]\s*(.*)/);
    if (m) return { meta: `${m[2]} • ${labelDate(m[1])}`, desc: m[3], raw };
    m = raw.match(/^(\d{2}[./]\d{2})\.?\s+(\d{1,2}[.:]\d{2})\s*[–\-]\s*(.*)/);
    if (m) return { meta: `${m[2]} • ${labelDate(`${m[1]}.${year}`)}`, desc: m[3], raw };
    m = raw.match(/^(\d{2}[./]\d{2})\.?\s*[–\-]\s*(.*)/);
    if (m) return { meta: labelDate(`${m[1]}.${year}`), desc: m[2], raw };
    return { meta: "", desc: raw, raw };
  }).filter((x) => x.raw);
}

function labelDate(dateStr: string): string {
  const parts = dateStr.replace(/\//g, ".").split(".");
  if (parts.length >= 3) return `${parts[0]} ${MONTHS[parts[1].padStart(2, "0")] ?? parts[1]} ${parts[2]}`;
  return dateStr;
}

function fieldAttr(name: keyof PresentationFields, editing: boolean): string {
  return `data-field="${name}" ${editing ? 'contenteditable="true"' : 'contenteditable="false"'}`;
}

export function renderVisualPresentation(
  fields: PresentationFields,
  opts: VisualPresentationOptions,
): HTMLElement {
  const root = document.createElement("div");
  root.className = `vp-root${opts.editing ? " vp-editing" : ""}`;
  const { team, person } = extractTeamAndPerson(fields.team);
  const losses = formatLossesDisplay(fields.losses);
  const lossClass = losses === "На оценке" ? "loss-na" : "";
  const system = extractCoreSystem(fields.chain);
  const responsible = extractPersons(fields.team);
  const chainLines = normalizeChainLines(fields.chain);
  const opRows = parseMeasures(fields.operational_measures);
  const sysRows = parseMeasures(fields.systemic_measures);
  const timeline = parseTimeline(fields.timeline ?? []);
  const tabLabel = fields.number.toUpperCase().startsWith("EVE-") ? fields.number : `EVE-${fields.number}`;

  root.innerHTML = `
    <div class="vp-header">
      <div>
        <div class="vp-tribe">Трайб Риски Розничного Бизнеса</div>
        <h2 class="vp-title">Комиссия по инцидентам</h2>
      </div>
      <div class="vp-tabs">
        <button type="button" class="vp-tab active" data-tab="summary">Содержание</button>
        <button type="button" class="vp-tab" data-tab="incident">${esc(tabLabel)}</button>
      </div>
    </div>

    <div class="vp-panel active" data-panel="summary">
      <div class="vp-summary-title">Инцидент</div>
      <table class="vp-summary-table">
        <thead><tr>
          <th>ИОР</th><th>АС/ФП</th><th>Суть инцидента</th><th>Ответственный</th><th>Потери</th>
        </tr></thead>
        <tbody><tr>
          <td ${fieldAttr("number", opts.editing)}>${esc(fields.number)}</td>
          <td>${esc(system)}</td>
          <td ${fieldAttr("brief", opts.editing)}>${esc(fields.brief)}</td>
          <td>${esc(responsible)}</td>
          <td class="${lossClass}" ${fieldAttr("losses", opts.editing)}>${esc(losses === "На оценке" ? fields.losses || "На оценке" : losses)}</td>
        </tr></tbody>
      </table>
    </div>

    <div class="vp-panel" data-panel="incident">
      <div class="vp-grid">
        <div class="vp-left">
          <div class="vp-info-strip">
            ${infoItem("ИОР", fields.number, "number", opts.editing)}
            ${infoItem("Команда", team, "team", false)}
            ${infoItem("Ответственный", person, "team", false)}
            ${infoItem("Юнит", fields.unit, "unit", opts.editing)}
            ${infoItem("Этап процесса", fields.stage, "stage", opts.editing)}
          </div>
          ${opts.editing ? `<label class="vp-label">Команда / ответственный (через запятую)</label>
            <div class="vp-card vp-text" ${fieldAttr("team", true)}>${esc(fields.team)}</div>` : ""}
          ${detail("Описание", fields.description, "description", opts.editing)}
          ${detail("Причина", fields.cause, "cause", opts.editing)}
          <div class="vp-card" data-section="chain">
            <div class="vp-card-title">Цепочка событий</div>
            ${chainLines.length
              ? chainLines.map((l, i) => `<div class="vp-chain${i === 0 ? " first" : ""}">${esc(l)}</div>`).join("")
              : `<div class="vp-text">${DASH}</div>`}
            ${opts.editing ? `<div class="vp-text vp-chain-edit" ${fieldAttr("chain", true)}>${esc(fields.chain)}</div>` : ""}
          </div>
          ${detail("Влияние на продукт", fields.impact, "impact", opts.editing)}
          ${measuresBlock("Оперативные мероприятия", opRows, "operational_measures", fields.operational_measures, opts.editing)}
          ${measuresBlock("Системные мероприятия", sysRows, "systemic_measures", fields.systemic_measures, opts.editing)}
        </div>
        <div class="vp-right">
          <div class="vp-loss">
            <div class="vp-loss-label">Потери</div>
            <div class="vp-loss-value ${lossClass}" ${fieldAttr("losses", opts.editing)}>${esc(losses)}</div>
          </div>
          <div class="vp-timeline">
            <div class="vp-timeline-header">Хронология</div>
            <div class="vp-timeline-body" data-timeline></div>
          </div>
        </div>
      </div>
    </div>
  `;

  const body = root.querySelector("[data-timeline]") as HTMLElement;
  if (!timeline.length) {
    body.innerHTML = `<div class="vp-tl-item"><div class="vp-tl-main">Хронология не указана</div></div>`;
  } else {
    for (const item of timeline) {
      body.appendChild(timelineRow(item.raw, item.meta, item.desc, opts.editing));
    }
  }
  if (opts.editing) {
    const add = document.createElement("button");
    add.type = "button";
    add.className = "vp-add-event";
    add.textContent = "+ добавить событие";
    add.addEventListener("click", () => {
      body.appendChild(timelineRow("", "", "", true));
    });
    body.after(add);
  }

  root.querySelectorAll<HTMLButtonElement>(".vp-tab").forEach((btn) => {
    btn.addEventListener("click", () => {
      root.querySelectorAll(".vp-tab").forEach((b) => b.classList.toggle("active", b === btn));
      root.querySelectorAll(".vp-panel").forEach((p) => {
        p.classList.toggle("active", (p as HTMLElement).dataset.panel === btn.dataset.tab);
      });
    });
  });

  return root;
}

function infoItem(label: string, value: string, field: keyof PresentationFields, editing: boolean): string {
  return `<div class="vp-info"><div class="vp-info-label">${esc(label)}</div>
    <div class="vp-info-value" ${fieldAttr(field, editing)}>${esc(value)}</div></div>`;
}

function detail(title: string, value: string, field: keyof PresentationFields, editing: boolean): string {
  return `<div class="vp-card"><div class="vp-card-title">${esc(title)}</div>
    <div class="vp-text" ${fieldAttr(field, editing)}>${esc(value)}</div></div>`;
}

function measuresBlock(
  title: string,
  rows: Array<{ measure: string; responsible: string }>,
  field: keyof PresentationFields,
  raw: string,
  editing: boolean,
): string {
  const hideResp = !rows.length || rows.every((r) => !r.responsible || r.responsible === DASH);
  const body = (rows.length ? rows : [{ measure: DASH, responsible: DASH }])
    .map((r) => `<div class="vp-mrow${hideResp ? " one" : ""}"><div>${esc(r.measure)}</div>${hideResp ? "" : `<div>${esc(r.responsible)}</div>`}</div>`)
    .join("");
  return `<div class="vp-card">
    <div class="vp-card-title">${esc(title)}</div>
    <div class="vp-mhead${hideResp ? " one" : ""}"><div>Мера</div>${hideResp ? "" : "<div>Ответственный и срок</div>"}</div>
    ${body}
    ${editing ? `<div class="vp-text" ${fieldAttr(field, true)}>${esc(raw)}</div>` : ""}
  </div>`;
}

function timelineRow(raw: string, meta: string, desc: string, editing: boolean): HTMLElement {
  const el = document.createElement("div");
  el.className = "vp-tl-item";
  el.innerHTML = editing
    ? `<input class="vp-tl-input" data-tl-raw placeholder="ДД.MM ЧЧ:ММ – описание" value="${esc(raw).replace(/<br>/g, "")}" />`
    : `<div class="vp-tl-dot"></div><div class="vp-tl-content">${meta ? `<div class="vp-tl-meta">${esc(meta)}</div>` : ""}<div class="vp-tl-main">${esc(desc || raw)}</div></div>`;
  return el;
}

export function collectFieldsFromSlide(root: HTMLElement, fallback: PresentationFields): PresentationFields {
  const read = (name: keyof PresentationFields): string => {
    const nodes = root.querySelectorAll<HTMLElement>(`[data-field="${name}"]`);
    if (!nodes.length) return fallback[name] as string;
    const last = nodes[nodes.length - 1];
    return (last.innerText || last.textContent || "").trim() || DASH;
  };

  const timeline = Array.from(root.querySelectorAll<HTMLInputElement>("[data-tl-raw]"))
    .map((i) => i.value.trim())
    .filter(Boolean);

  return {
    number: read("number"),
    unit: read("unit"),
    team: read("team"),
    brief: read("brief"),
    description: read("description"),
    cause: read("cause"),
    chain: read("chain"),
    stage: read("stage"),
    impact: read("impact"),
    losses: read("losses"),
    operational_measures: read("operational_measures"),
    systemic_measures: read("systemic_measures"),
    timeline: timeline.length ? timeline : fallback.timeline,
  };
}
