import assert from "node:assert/strict";
import { applyEvent, emptyState } from "../events.mjs";
import { unreadCount } from "../unread.mjs";

const msg = (thread, id, from = "ana") => ({ type: "message", thread, id, from });

export function testEditsAndReplaysCountOnce() {
  const s = emptyState("jake");
  for (const ev of [msg("f", 1), { type: "edit", thread: "f", id: 1 }, { type: "edit", thread: "f", id: 1 },
                    msg("f", 2), msg("f", 1), msg("f", 2)]) applyEvent(s, ev);
  assert.equal(unreadCount(s, "f"), 2);
}

export function testDeletedMessagesStayGone() {
  const s = emptyState("jake");
  applyEvent(s, msg("f", 1));
  applyEvent(s, msg("f", 2));
  applyEvent(s, { type: "delete", thread: "f", id: 1 });
  assert.equal(unreadCount(s, "f"), 1);
  applyEvent(s, msg("f", 1));                       // replayed after the delete
  assert.equal(unreadCount(s, "f"), 1);
  applyEvent(s, { type: "delete", thread: "f", id: 7 });   // unknown id: harmless
  assert.equal(unreadCount(s, "f"), 1);
}
