import assert from "node:assert/strict";
import { render } from "../mdlite.mjs";

export function testParagraphs() {
  assert.equal(render("a\nb\n\nc"), "<p>a<br>b</p><p>c</p>");
}

export function testInline() {
  assert.equal(render("**x** *y* `<z>`"), "<p><strong>x</strong> <em>y</em> <code>&lt;z&gt;</code></p>");
}
