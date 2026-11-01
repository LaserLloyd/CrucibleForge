// mdlite: tiny, safe Markdown for chat bubbles.

const ESC = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };

export function escapeHtml(s) {
  return s.replace(/[&<>"']/g, (c) => ESC[c]);
}

function inline(text) {
  // code spans first: their content is escaped and left alone
  const parts = text.split(/(`[^`]+`)/g);
  return parts
    .map((part) => {
      if (part.length > 2 && part.startsWith("`") && part.endsWith("`")) {
        return "<code>" + escapeHtml(part.slice(1, -1)) + "</code>";
      }
      let s = escapeHtml(part);
      s = s.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
      s = s.replace(/\*([^*]+)\*/g, "<em>$1</em>");
      return s;
    })
    .join("");
}

export function render(text) {
  return text
    .replace(/\r\n/g, "\n")
    .split(/\n{2,}/)
    .filter((p) => p.trim() !== "")
    .map((p) => "<p>" + p.split("\n").map(inline).join("<br>") + "</p>")
    .join("");
}
