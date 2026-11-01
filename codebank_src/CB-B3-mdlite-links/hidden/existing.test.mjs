import assert from "node:assert/strict";
import { escapeHtml, render } from "../mdlite.mjs";

export function testParagraphsAndBreaks() {
  assert.equal(render("a\r\nb\n\n\n\nc\n\n  \n"), "<p>a<br>b</p><p>c</p>");
}

export function testEscapingEverywhere() {
  assert.equal(render(`<script>"x" & 'y'</script>`),
    "<p>&lt;script&gt;&quot;x&quot; &amp; &#39;y&#39;&lt;/script&gt;</p>");
  assert.equal(escapeHtml("<&>"), "&lt;&amp;&gt;");
}

export function testInlineFormats() {
  assert.equal(render("**b** *e* `**not bold**` [plain"),
    "<p><strong>b</strong> <em>e</em> <code>**not bold**</code> [plain</p>");
}
