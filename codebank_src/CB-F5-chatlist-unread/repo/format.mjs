// "5 min ago" style labels for the thread list.

export function relativeTime(thenMs, nowMs) {
  const s = Math.max(0, Math.floor((nowMs - thenMs) / 1000));
  if (s < 60) return "now";
  if (s < 3600) return `${Math.floor(s / 60)} min`;
  if (s < 86400) return `${Math.floor(s / 3600)} h`;
  return `${Math.floor(s / 86400)} d`;
}
