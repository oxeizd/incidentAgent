/**
 * marked/DOMPurify/hljs подключены через CDN <script> в index.html (как и в
 * старом фронтенде) — не тащим их в npm-зависимости, чтобы не плодить
 * лишний бандл ради трёх готовых библиотек рендера markdown/санитайзинга.
 */
declare const marked: { parse(src: string): string; setOptions(opts: Record<string, unknown>): void };
declare const DOMPurify: { sanitize(html: string): string };
declare const hljs: { highlightElement(el: Element): void };
