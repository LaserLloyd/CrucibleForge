import assert from "node:assert/strict";
import { applyEvent, emptyState } from "../events.mjs";
import { relativeTime } from "../format.mjs";
import { totalUnread, unreadCount } from "../unread.mjs";

export function testPlainFlow() {
  const s = emptyState("jake");
  assert.equal(unreadCount(s, "none"), 0);
  applyEvent(s, { type: "message", thread: "a", id: 1, from: "x" });
  applyEvent(s, { type: "message", thread: "b", id: 1, from: "y" });
  applyEvent(s, { type: "read", thread: "a", upTo: 1 });
  assert.equal(totalUnread(s), 1);
  assert.equal(applyEvent(s, { type: "read", thread: "b", upTo: 1 }), s);
}

export function testRelativeTime() {
  assert.deepEqual([0, 61_000, 3_600_000, 90_000_000, -5].map((d) => relativeTime(0, d)),
                   ["now", "1 min", "1 h", "1 d", "now"]);
}
