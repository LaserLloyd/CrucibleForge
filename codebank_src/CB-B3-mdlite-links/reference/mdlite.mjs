// mdlite: tiny, safe Markdown for chat bubbles.

const ESC = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };

export function escapeHtml(s) {
  return s.replace(/[&<>"']/g, (c) => ESC[c]);
}

function formatText(part) {
  let s = escapeHtml(part);
  s = s.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
  s = s.replace(/\*([^*]+)\*/g, "<em>$1</em>");
  return s;
}

function codeAndText(text) {
  return text
    .split(/(`[^`]+`)/g)
    .map((part) =>
      part.length > 2 && part.startsWith("`") && part.endsWith("`")
        ? "<code>" + escapeHtml(part.slice(1, -1)) + "</code>"
        : formatText(part),
    )
    .join("");
}

export function safeUrl(url) {
  const m = /^([A-Za-z][A-Za-z0-9+.-]*):/.exec(url);
  return !m || ["http", "https", "mailto"].includes(m[1].toLowerCase());
}

function inline(text) {
  // code spans are opaque: split them out first, link only in the rest
  const parts = text.split(/(`[^`]+`)/g);
  return parts
    .map((part) => {
      if (part.length > 2 && part.startsWith("`") && part.endsWith("`")) {
        return "<code>" + escapeHtml(part.slice(1, -1)) + "</code>";
      }
      let out = "";
      let last = 0;
      const re = /\[([^\]]*)\]\(([^\s)]+)\)/g;
      let m;
      while ((m = re.exec(part)) !== null) {
        out += formatText(part.slice(last, m.index));
        const label = formatText(m[1]);
        out += safeUrl(m[2])
          ? '<a href="' + escapeHtml(m[2]) + '" rel="nofollow noopener">' + label + "</a>"
          : label;
        last = m.index + m[0].length;
      }
      return out + formatText(part.slice(last));
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
