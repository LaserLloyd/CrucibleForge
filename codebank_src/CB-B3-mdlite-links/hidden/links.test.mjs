import assert from "node:assert/strict";
import { render } from "../mdlite.mjs";

const A = (href, text) => `<a href="${href}" rel="nofollow noopener">${text}</a>`;

export function testBasic() {
  assert.equal(render("see [the docs](https://example.org/a?b=1&c=2) now"),
    `<p>see ${A("https://example.org/a?b=1&amp;c=2", "the docs")} now</p>`);
}

export function testSchemesAndRelative() {
  assert.equal(render("[m](mailto:ana@example.org)"), `<p>${A("mailto:ana@example.org", "m")}</p>`);
  assert.equal(render("[up](../index.html) [top](#top) [p](page.html) [r](/x/y)"),
    `<p>${A("../index.html", "up")} ${A("#top", "top")} ${A("page.html", "p")} ${A("/x/y", "r")}</p>`);
  assert.equal(render("[x](HTTP://EXAMPLE.ORG)"), `<p>${A("HTTP://EXAMPLE.ORG", "x")}</p>`);
}

export function testLabelFormattingAndSeveralLinks() {
  assert.equal(render("[**big** *it*](http://a.b) and [two](http://c.d)\nnext"),
    `<p>${A("http://a.b", "<strong>big</strong> <em>it</em>")} and ${A("http://c.d", "two")}<br>next</p>`);
}

export function testNotALink() {
  assert.equal(render("[a](has space) [b]() [c] (http://x)"), "<p>[a](has space) [b]() [c] (http://x)</p>");
}
