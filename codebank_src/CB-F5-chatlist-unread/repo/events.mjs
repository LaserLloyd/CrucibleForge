// Folding server events into the client state.

export function emptyState(me) {
  return { me, threads: {} };
}

function thread(state, id) {
  if (!state.threads[id]) state.threads[id] = { messages: [], lastRead: 0 };
  return state.threads[id];
}

export function applyEvent(state, ev) {
  const t = thread(state, ev.thread);
  switch (ev.type) {
    case "message":
    case "edit":
      t.messages.push({ id: ev.id, from: ev.from });
      break;
    case "delete":
      break;
    case "read":
      t.lastRead = ev.upTo;
      break;
  }
  return state;
}
