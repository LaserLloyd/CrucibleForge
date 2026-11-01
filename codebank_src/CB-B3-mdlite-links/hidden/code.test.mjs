import assert from "node:assert/strict";
import { render } from "../mdlite.mjs";

export function testNoLinksInCode() {
  assert.equal(render("`[a](http://b)` and [c](http://d)"),
    '<p><code>[a](http://b)</code> and <a href="http://d" rel="nofollow noopener">c</a></p>');
}

export function testHtmlInLabelEscaped() {
  assert.equal(render("[<b>hi</b>](http://x)"),
    '<p><a href="http://x" rel="nofollow noopener">&lt;b&gt;hi&lt;/b&gt;</a></p>');
}
