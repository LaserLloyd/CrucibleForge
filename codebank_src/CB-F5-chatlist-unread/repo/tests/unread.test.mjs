import assert from "node:assert/strict";
import { applyEvent, emptyState } from "../events.mjs";
import { totalUnread, unreadCount } from "../unread.mjs";

export function testCountsNewMessages() {
  const s = emptyState("jake");
  applyEvent(s, { type: "message", thread: "fam", id: 1, from: "ana" });
  applyEvent(s, { type: "message", thread: "fam", id: 2, from: "ben" });
  assert.equal(unreadCount(s, "fam"), 2);
  applyEvent(s, { type: "read", thread: "fam", upTo: 1 });
  assert.equal(totalUnread(s), 1);
}

export function testEditIsNotANewMessage() {
  const s = emptyState("jake");
  applyEvent(s, { type: "message", thread: "fam", id: 1, from: "ana" });
  applyEvent(s, { type: "edit", thread: "fam", id: 1 });
  assert.equal(unreadCount(s, "fam"), 1);
}
