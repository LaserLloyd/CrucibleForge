import assert from "node:assert/strict";
import { render } from "../mdlite.mjs";

export function testBadSchemesRenderTextOnly() {
  for (const url of ["javascript:void0", "JavaScript:void0", "JAVASCRIPT:x", "data:text/html,hi",
                     "vbscript:msgbox", "file:///etc/passwd", "jav4script:x", "blob:abc"]) {
    assert.equal(render(`[click **me**](${url})`), "<p>click <strong>me</strong></p>", url);
  }
}

export function testQuotesInUrlCannotBreakOut() {
  const out = render('[x](http://a.b/"onmouseover=alert`1`)');
  assert.ok(!out.includes('"onmouseover'), out);
  const out2 = render("[x](http://a.b/'onfocus=x)");
  assert.ok(out2.includes('href="http://a.b/&#39;onfocus=x"'), out2);
}

export function testSchemeLookalikesAreRelative() {
  assert.equal(render("[x](javascript%3Avoid0)"),
    '<p><a href="javascript%3Avoid0" rel="nofollow noopener">x</a></p>');
  assert.equal(render("[x](./javascript:void0)"),
    '<p><a href="./javascript:void0" rel="nofollow noopener">x</a></p>');
}
