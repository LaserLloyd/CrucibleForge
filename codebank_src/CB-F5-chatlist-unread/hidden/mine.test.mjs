import assert from "node:assert/strict";
import { applyEvent, emptyState } from "../events.mjs";
import { badgeText, totalUnread } from "../unread.mjs";

export function testOwnMessagesNeverUnread() {
  const s = emptyState("jake");
  applyEvent(s, { type: "message", thread: "f", id: 1, from: "jake" });
  applyEvent(s, { type: "message", thread: "f", id: 2, from: "ana" });
  applyEvent(s, { type: "message", thread: "h", id: 9, from: "jake" });
  assert.equal(totalUnread(s), 1);
}

export function testBadge() {
  assert.deepEqual([0, 1, 99, 100, 250].map(badgeText), ["", "1", "99", "99+", "99+"]);
}
