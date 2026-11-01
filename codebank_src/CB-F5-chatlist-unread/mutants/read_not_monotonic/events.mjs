// Folding server events into the client state.

export function emptyState(me) {
  return { me, threads: {} };
}

function thread(state, id) {
  if (!state.threads[id]) state.threads[id] = { messages: [], lastRead: 0, deleted: [] };
  return state.threads[id];
}

export function applyEvent(state, ev) {
  const t = thread(state, ev.thread);
  switch (ev.type) {
    case "message":
      if (!t.deleted.includes(ev.id) && !t.messages.some((m) => m.id === ev.id)) {
        t.messages.push({ id: ev.id, from: ev.from });
      }
      break;
    case "edit":
      break;
    case "delete":
      t.messages = t.messages.filter((m) => m.id !== ev.id);
      if (!t.deleted.includes(ev.id)) t.deleted.push(ev.id);
      break;
    case "read":
      t.lastRead = ev.upTo;
      break;
  }
  return state;
}
