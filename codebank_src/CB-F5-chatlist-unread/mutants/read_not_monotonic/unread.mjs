// Unread counts and badge text.

export function unreadCount(state, threadId) {
  const t = state.threads[threadId];
  if (!t) return 0;
  return t.messages.filter((m) => m.id > t.lastRead && m.from !== state.me).length;
}

export function totalUnread(state) {
  return Object.keys(state.threads).reduce((sum, id) => sum + unreadCount(state, id), 0);
}

export function badgeText(n) {
  if (n <= 0) return "";
  return n > 99 ? "99+" : String(n);
}
