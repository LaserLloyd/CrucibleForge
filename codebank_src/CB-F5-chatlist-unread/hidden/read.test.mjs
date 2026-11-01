import assert from "node:assert/strict";
import { applyEvent, emptyState } from "../events.mjs";
import { totalUnread, unreadCount } from "../unread.mjs";

export function testReadMarkNeverGoesBack() {
  const s = emptyState("jake");
  for (let i = 1; i <= 5; i++) applyEvent(s, { type: "message", thread: "f", id: i, from: "ana" });
  applyEvent(s, { type: "read", thread: "f", upTo: 4 });   // phone
  applyEvent(s, { type: "read", thread: "f", upTo: 2 });   // late event from the laptop
  assert.equal(unreadCount(s, "f"), 1);
  applyEvent(s, { type: "read", thread: "g", upTo: 3 });   // read before any message arrived
  applyEvent(s, { type: "message", thread: "g", id: 2, from: "ben" });
  applyEvent(s, { type: "message", thread: "g", id: 4, from: "ben" });
  assert.equal(totalUnread(s), 2);
}
