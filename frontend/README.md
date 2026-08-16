# incAgent — frontend (TypeScript)

Замена `app/api/static/index.html` (vanilla JS без типов и без поддержки
интерактивных interrupt'ов) на модульный TypeScript-фронтенд, полностью
покрывающий текущий API-контракт бэкенда.

## Что нового по сравнению со старым `index.html`

Старый фронтенд (см. `paste.txt` в истории разработки) отправлял сообщения
только через `POST /api/v1/threads/{id}/messages` — этот эндпоинт **не
поддерживает** structured `payload` (`app/api/app.py:post_thread_message`
всегда передаёт `structured_payload=None`), и никогда не проверял
`awaiting_input`/`tool_calls` — вопрос агента (question/confirmation/form)
показывался как обычный текст, ответ пользователя всегда уходил как
`Command(resume=text)`.

Новый фронтенд:

- переходит на `POST /message` (SSE) — единственный эндпоинт, поддерживающий
  и текст, и `payload`, и `tool_call_id` (см. `MessageRequest` в
  `app/api/schemas.py`);
- рендерит `tool_calls[0]` по имени функции (`ask_user`/`ask_confirmation`/
  `ask_form`) — свободный вопрос, да/нет-кнопки, форма с полями по их
  `type`/`items` (см. `src/render/interactive.ts`);
- при загрузке истории треда явно проверяет `GET /threads/{id}`
  (`ThreadStateResponse.awaiting_input`/`tool_calls`) — если тред ждёт
  ответа, виджет показывается сразу, а не молчит об этом;
- добавляет ссылку "Скачать файл" под артефактами с HTML-секцией — ведёт на
  новый `GET /api/v1/threads/{id}/artifacts/{id}/file`
  (`Content-Disposition: attachment`).

## Запуск

```bash
cd frontend
npm install
npm run dev      # дев-сервер на 5173, проксирует /api,/message,/threads на localhost:8000
npm run build     # собирает в frontend/dist — можно скопировать в app/api/static/
```

## Структура

```
src/
  types.ts             — контракт API (зеркало app/api/schemas.py, sse.py)
  api.ts               — типизированный HTTP/SSE-клиент
  render/
    chat.ts            — сообщения, артефакты, ссылка на скачивание
    sidebar.ts          — список тредов, typing-индикатор
    interactive.ts      — question/confirmation/form по tool_calls
    markdown.ts         — marked+DOMPurify+hljs обёртки
  main.ts              — состояние приложения, склейка всего
  styles.css
```

## Известные ограничения (следующие шаги)

- Экспорт истории треда в `.txt` (был в старом `index.html`) пока не
  перенесён — тривиально добавить через уже существующий `getMessages()`.
- Object-поля формы (`type: "object"`) редактируются как сырой JSON —
  нет отдельного UI-конструктора вложенных полей (на сегодня в реальной
  схеме `ExtractedIncidentData` таких полей нет).
- AG-UI endpoint (`app/api/agui.py`) этим фронтендом не используется —
  он для внешних потребителей (Grafana-плагин и т.п.), не для этого чата.
