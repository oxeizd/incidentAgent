import type {
  AskConfirmationArgs,
  AskFormArgs,
  AskUserArgs,
  FormField,
  InteractiveOption,
  ToolCall,
} from "../types";

export type InterruptAnswer =
  | { kind: "text"; text: string; displayText?: string }
  | { kind: "payload"; payload: Record<string, unknown>; displayText: string };

export interface InterruptRenderOptions {
  toolCall: ToolCall;
  disabled: boolean;
  initialValues?: Record<string, unknown>;
  errorMessage?: string | null;
  onValuesChange?: (values: Record<string, unknown>) => void;
  onSubmit: (answer: InterruptAnswer, toolCallId: string) => void;
}

export function parseInterruptArguments(raw: string): Record<string, unknown> | null {
  try {
    const value: unknown = JSON.parse(raw || "{}");
    if (!value || typeof value !== "object" || Array.isArray(value)) return null;
    return value as Record<string, unknown>;
  } catch {
    return null;
  }
}

export function renderInterruptInto(container: HTMLElement, options: InterruptRenderOptions): void {
  container.replaceChildren();

  const args = parseInterruptArguments(options.toolCall.function.arguments);
  if (!args) {
    renderFallback(container, options);
    return;
  }

  switch (options.toolCall.function.name) {
    case "ask_confirmation":
      renderOptions(container, args as AskConfirmationArgs, options, true);
      return;
    case "ask_form":
      renderForm(container, args as AskFormArgs, options);
      return;
    case "ask_user":
    default:
      if (Array.isArray((args as AskUserArgs).options) && (args as AskUserArgs).options!.length > 0) {
        renderOptions(container, args as AskUserArgs, options, false);
      } else {
        renderTextQuestion(container, args as AskUserArgs, options);
      }
  }
}

function createComposer(question: string, disabled: boolean): HTMLDivElement {
  const root = document.createElement("div");
  root.className = "interactive-composer";
  root.classList.toggle("interactive-loading", disabled);

  const label = document.createElement("p");
  label.className = "interactive-question-label";
  label.textContent = question || "Уточните ответ";
  label.setAttribute("aria-live", "polite");
  root.appendChild(label);
  return root;
}

function appendGlobalError(root: HTMLElement, message?: string | null): void {
  if (!message) return;
  const error = document.createElement("div");
  error.className = "interactive-error";
  error.setAttribute("role", "status");
  error.textContent = message;
  root.appendChild(error);
}

function renderFallback(container: HTMLElement, options: InterruptRenderOptions): void {
  const root = createComposer("Не удалось отобразить уточняющий вопрос.", options.disabled);
  const hint = document.createElement("p");
  hint.className = "interactive-fallback-hint";
  hint.textContent = "Напишите ответ текстом.";
  root.appendChild(hint);
  appendGlobalError(root, options.errorMessage);

  const input = document.createElement("textarea");
  input.className = "interactive-text-input";
  input.rows = 1;
  input.placeholder = "Ваш ответ...";
  input.value = stringValue(options.initialValues?.text);
  input.disabled = options.disabled;

  const submit = document.createElement("button");
  submit.className = "interactive-submit";
  submit.type = "button";
  submit.textContent = "Ответить";
  submit.disabled = options.disabled || !input.value.trim();

  const update = () => {
    options.onValuesChange?.({ text: input.value });
    submit.disabled = options.disabled || !input.value.trim();
  };
  input.addEventListener("input", update);
  submit.addEventListener("click", () => {
    const text = input.value.trim();
    if (text) options.onSubmit({ kind: "text", text }, options.toolCall.id);
  });
  input.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      submit.click();
    }
  });

  const row = document.createElement("div");
  row.className = "interactive-actions interactive-text-actions";
  row.append(input, submit);
  root.appendChild(row);
  container.appendChild(root);
  if (!options.disabled) queueMicrotask(() => input.focus());
}

function renderTextQuestion(
  container: HTMLElement,
  args: AskUserArgs,
  options: InterruptRenderOptions,
): void {
  const root = createComposer(args.question, options.disabled);
  appendGlobalError(root, options.errorMessage);

  const input = document.createElement("textarea");
  input.className = "interactive-text-input";
  input.rows = 1;
  input.placeholder = "Ваш ответ...";
  input.value = stringValue(options.initialValues?.text);
  input.disabled = options.disabled;

  const submit = document.createElement("button");
  submit.className = "interactive-submit";
  submit.type = "button";
  submit.textContent = args.submitLabel || "Ответить";
  submit.disabled = options.disabled || !input.value.trim();

  const update = () => {
    options.onValuesChange?.({ text: input.value });
    submit.disabled = options.disabled || !input.value.trim();
  };
  input.addEventListener("input", update);
  submit.addEventListener("click", () => {
    const text = input.value.trim();
    if (text) options.onSubmit({ kind: "text", text }, options.toolCall.id);
  });
  input.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      submit.click();
    }
  });

  const row = document.createElement("div");
  row.className = "interactive-actions interactive-text-actions";
  row.append(input, submit);
  root.appendChild(row);
  container.appendChild(root);
  if (!options.disabled) queueMicrotask(() => input.focus());
}

function renderOptions(
  container: HTMLElement,
  args: AskUserArgs | AskConfirmationArgs,
  options: InterruptRenderOptions,
  isConfirmation: boolean,
): void {
  const root = createComposer(args.question, options.disabled);
  appendGlobalError(root, options.errorMessage);

  const sourceOptions = args.options?.length
    ? args.options
    : isConfirmation
      ? [{ label: "Да", value: "confirm" }, { label: "Нет", value: "reject" }]
      : [];
  const selectedCustom = options.initialValues?.customOption === true;
  const customText = stringValue(options.initialValues?.customText);

  const group = document.createElement("div");
  group.className = "interactive-options";
  group.setAttribute("role", "group");
  group.setAttribute("aria-label", "Ответ на вопрос");

  sourceOptions.forEach((option) => {
    if (selectedCustom && option.allowCustom) {
      group.appendChild(renderCustomOption(option, args, options, isConfirmation));
      return;
    }

    const button = document.createElement("button");
    button.className = "interactive-option";
    button.type = "button";
    button.textContent = option.label;
    button.disabled = options.disabled;
    button.addEventListener("click", () => {
      if (option.allowCustom) {
        options.onValuesChange?.({ customOption: true, customText });
        return;
      }
      submitOption(option, options, isConfirmation);
    });
    group.appendChild(button);
  });

  root.appendChild(group);
  container.appendChild(root);

  if (!options.disabled) {
    queueMicrotask(() => root.querySelector<HTMLElement>("input, button")?.focus());
  }
}

function renderCustomOption(
  option: InteractiveOption,
  args: AskUserArgs | AskConfirmationArgs,
  options: InterruptRenderOptions,
  isConfirmation: boolean,
): HTMLElement {
  const item = document.createElement("div");
  item.className = "interactive-custom-option";

  const input = document.createElement("input");
  input.type = "text";
  input.placeholder = option.label || "Ваш вариант";
  input.value = stringValue(options.initialValues?.customText);
  input.disabled = options.disabled;

  const submit = document.createElement("button");
  submit.className = "interactive-submit";
  submit.type = "button";
  submit.textContent = args.submitLabel || "Ответить";
  submit.disabled = options.disabled || !input.value.trim();

  const back = document.createElement("button");
  back.className = "interactive-back";
  back.type = "button";
  back.textContent = "Назад к вариантам";
  back.disabled = options.disabled;

  const update = () => {
    options.onValuesChange?.({ customOption: true, customText: input.value });
    submit.disabled = options.disabled || !input.value.trim();
  };
  input.addEventListener("input", update);
  input.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      event.preventDefault();
      options.onValuesChange?.({ customOption: false, customText: "" });
    }
    if (event.key === "Enter") {
      event.preventDefault();
      submit.click();
    }
  });
  back.addEventListener("click", () => options.onValuesChange?.({ customOption: false, customText: "" }));
  submit.addEventListener("click", () => {
    const text = input.value.trim();
    if (!text) return;
    if (isConfirmation) {
      options.onSubmit({
        kind: "payload",
        payload: { confirmed: false, value: text },
        displayText: text,
      }, options.toolCall.id);
    } else {
      options.onSubmit({ kind: "text", text }, options.toolCall.id);
    }
  });

  const actions = document.createElement("div");
  actions.className = "interactive-actions";
  actions.append(back, submit);
  item.append(input, actions);
  queueMicrotask(() => { if (!options.disabled) input.focus(); });
  return item;
}

function submitOption(
  option: InteractiveOption,
  options: InterruptRenderOptions,
  isConfirmation: boolean,
): void {
  const value = option.value ?? option.label;
  if (isConfirmation) {
    const normalized = value.trim().toLowerCase();
    const confirmed = normalized === "confirm" || normalized === "yes" || normalized === "да" || normalized === "true";
    options.onSubmit({
      kind: "payload",
      payload: { confirmed, value },
      displayText: option.label,
    }, options.toolCall.id);
    return;
  }
  options.onSubmit({ kind: "text", text: value, displayText: option.label }, options.toolCall.id);
}

function renderForm(container: HTMLElement, args: AskFormArgs, options: InterruptRenderOptions): void {
  const root = createComposer(args.question, options.disabled);
  appendGlobalError(root, options.errorMessage);

  const form = document.createElement("form");
  form.className = "interactive-form";
  form.noValidate = true;

  const values: Record<string, unknown> = {};
  const controls = new Map<string, HTMLElement>();
  for (const field of args.fields) {
    values[field.name] = options.initialValues?.[field.name] ?? field.value ?? defaultValue(field);
  }

  const notify = () => options.onValuesChange?.({ ...values });
  for (const field of args.fields) {
    const rendered = renderField(field, values, options.disabled, notify);
    controls.set(field.name, rendered.control);
    form.appendChild(rendered.element);
  }

  const actions = document.createElement("div");
  actions.className = "interactive-actions";
  const submit = document.createElement("button");
  submit.className = "interactive-submit";
  submit.type = "submit";
  submit.textContent = args.submitLabel || "Отправить";
  submit.disabled = options.disabled;
  actions.appendChild(submit);
  form.appendChild(actions);

  form.addEventListener("submit", (event) => {
    event.preventDefault();
    const parsed = collectFormValues(args.fields, values);
    clearFieldErrors(form);
    if (Object.keys(parsed.errors).length > 0) {
      renderFieldErrors(controls, parsed.errors);
      return;
    }
    options.onValuesChange?.(parsed.payload);
    options.onSubmit({
      kind: "payload",
      payload: parsed.payload,
      displayText: formatFormAnswer(args, parsed.payload),
    }, options.toolCall.id);
  });

  root.appendChild(form);
  container.appendChild(root);
  if (!options.disabled) {
    queueMicrotask(() => controls.values().next().value?.focus?.());
  }
}

function renderField(
  field: FormField,
  values: Record<string, unknown>,
  disabled: boolean,
  notify: () => void,
): { element: HTMLElement; control: HTMLElement } {
  const element = document.createElement("div");
  element.className = "interactive-field";

  const label = document.createElement("label");
  const inputId = `interactive-${field.name}`;
  label.htmlFor = inputId;
  label.textContent = `${field.label}${field.required ? " *" : ""}`;
  element.appendChild(label);

  const initial = values[field.name];
  let control: HTMLElement;

  if (field.type === "boolean") {
    const input = document.createElement("input");
    input.id = inputId;
    input.type = "checkbox";
    input.checked = Boolean(initial);
    input.disabled = disabled;
    input.addEventListener("change", () => { values[field.name] = input.checked; notify(); });
    control = input;
  } else if (field.type === "select") {
    const select = document.createElement("select");
    select.id = inputId;
    select.disabled = disabled;
    const placeholder = document.createElement("option");
    placeholder.value = "";
    placeholder.textContent = field.placeholder || "Выберите значение";
    select.appendChild(placeholder);
    for (const option of field.options ?? []) {
      const item = document.createElement("option");
      item.value = option;
      item.textContent = option;
      select.appendChild(item);
    }
    select.value = initial == null ? "" : String(initial);
    select.addEventListener("change", () => { values[field.name] = select.value || null; notify(); });
    control = select;
  } else if (field.type === "array") {
    const input = document.createElement("textarea");
    input.id = inputId;
    input.rows = 3;
    input.placeholder = field.placeholder || "По одному значению на строку";
    input.value = Array.isArray(initial) ? initial.map(String).join("\n") : "";
    input.disabled = disabled;
    input.addEventListener("input", () => { values[field.name] = input.value; notify(); });
    control = input;
  } else if (field.type === "object" || field.type === "textarea") {
    const input = document.createElement("textarea");
    input.id = inputId;
    input.rows = field.type === "object" ? 5 : 3;
    input.placeholder = field.placeholder || (field.type === "object" ? "Введите JSON-объект" : "");
    input.value = field.type === "object" ? objectText(initial) : stringValue(initial);
    input.disabled = disabled;
    input.addEventListener("input", () => { values[field.name] = input.value; notify(); });
    control = input;
  } else {
    const input = document.createElement("input");
    input.id = inputId;
    input.type = field.type === "integer" || field.type === "number" ? "number" : "text";
    if (field.type === "integer") input.step = "1";
    if (field.type === "number") input.step = "any";
    input.placeholder = field.placeholder || "";
    input.value = initial == null ? "" : String(initial);
    input.disabled = disabled;
    input.addEventListener("input", () => { values[field.name] = input.value; notify(); });
    control = input;
  }

  element.appendChild(control);
  return { element, control };
}

function collectFormValues(
  fields: FormField[],
  values: Record<string, unknown>,
): { payload: Record<string, unknown>; errors: Record<string, string> } {
  const payload: Record<string, unknown> = {};
  const errors: Record<string, string> = {};

  for (const field of fields) {
    const raw = values[field.name];
    let value: unknown = raw;

    if (field.type === "array") {
      value = typeof raw === "string"
        ? raw.split("\n").map((item) => item.trim()).filter(Boolean).map((item) => coerceArrayItem(item, field.items?.type))
        : (Array.isArray(raw) ? raw : []);
    } else if (field.type === "object") {
      const text = stringValue(raw).trim();
      if (!text) {
        value = null;
      } else {
        try {
          const parsed: unknown = JSON.parse(text);
          if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
            errors[field.name] = "Введите JSON-объект.";
            continue;
          }
          value = parsed;
        } catch {
          errors[field.name] = "Введите корректный JSON-объект.";
          continue;
        }
      }
    } else if (field.type === "integer" || field.type === "number") {
      const text = stringValue(raw).trim();
      if (!text) value = null;
      else {
        const number = Number(text);
        if (!Number.isFinite(number) || (field.type === "integer" && !Number.isInteger(number))) {
          errors[field.name] = field.type === "integer" ? "Введите целое число." : "Введите число.";
          continue;
        }
        value = number;
      }
    } else if (field.type !== "boolean") {
      value = stringValue(raw).trim() || null;
    }

    if (field.required && isEmpty(value)) {
      errors[field.name] = "Заполните обязательное поле.";
      continue;
    }
    payload[field.name] = value;
  }

  return { payload, errors };
}

function renderFieldErrors(controls: Map<string, HTMLElement>, errors: Record<string, string>): void {
  for (const [name, text] of Object.entries(errors)) {
    const control = controls.get(name);
    if (!control) continue;
    const errorId = `interactive-${name}-error`;
    control.setAttribute("aria-invalid", "true");
    control.setAttribute("aria-describedby", errorId);
    const error = document.createElement("div");
    error.className = "interactive-error";
    error.id = errorId;
    error.textContent = text;
    control.parentElement?.appendChild(error);
  }
  controls.get(Object.keys(errors)[0])?.focus();
}

function clearFieldErrors(form: HTMLElement): void {
  form.querySelectorAll(".interactive-field > .interactive-error").forEach((element) => element.remove());
  form.querySelectorAll<HTMLElement>("[aria-invalid]").forEach((element) => {
    element.removeAttribute("aria-invalid");
    element.removeAttribute("aria-describedby");
  });
}

function formatFormAnswer(args: AskFormArgs, payload: Record<string, unknown>): string {
  const lines = args.fields
    .filter((field) => !isEmpty(payload[field.name]))
    .map((field) => `• ${field.label}: ${formatValue(payload[field.name])}`);
  return lines.length ? `Заполненная форма:\n${lines.join("\n")}` : "Форма отправлена";
}

function formatValue(value: unknown): string {
  if (Array.isArray(value)) return value.map(formatValue).join("; ");
  if (value && typeof value === "object") return JSON.stringify(value);
  if (typeof value === "boolean") return value ? "Да" : "Нет";
  return String(value);
}

function defaultValue(field: FormField): unknown {
  if (field.type === "boolean") return false;
  if (field.type === "array") return [];
  return null;
}

function coerceArrayItem(value: string, type?: string): unknown {
  if (type !== "integer" && type !== "number") return value;
  const number = Number(value);
  return Number.isFinite(number) ? number : value;
}

function isEmpty(value: unknown): boolean {
  return value === null || value === undefined || value === "" || (Array.isArray(value) && value.length === 0);
}

function stringValue(value: unknown): string {
  return typeof value === "string" ? value : value == null ? "" : String(value);
}

function objectText(value: unknown): string {
  if (value == null) return "";
  if (typeof value === "string") return value;
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return "";
  }
}
