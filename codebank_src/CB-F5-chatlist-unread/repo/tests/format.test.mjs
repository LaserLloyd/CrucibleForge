import assert from "node:assert/strict";
import { relativeTime } from "../format.mjs";

export function testLabels() {
  assert.equal(relativeTime(0, 59_000), "now");
  assert.equal(relativeTime(0, 5 * 60_000), "5 min");
}
