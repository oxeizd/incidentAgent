export function renderMarkdown(content: string): string {
  let html: string;
  try {
    html = marked.parse(content);
  } catch {
    html = escapeHtml(content).replace(/\n/g, "<br>");
  }
  return DOMPurify.sanitize(html);
}

export function sanitizeHtml(html: string): string {
  return DOMPurify.sanitize(html);
}

export function escapeHtml(text: string): string {
  const div = document.createElement("div");
  div.textContent = text;
  return div.innerHTML;
}

export function highlightCodeBlocks(root: HTMLElement): void {
  root.querySelectorAll("pre code").forEach((block) => {
    if (typeof hljs !== "undefined") hljs.highlightElement(block);
  });
}
