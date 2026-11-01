// Service worker core: caching strategy, testable with injected env.

export async function onInstall(env) {
  const cache = await env.caches.open(env.CACHE);
  await cache.addAll(env.PRECACHE);
}

export async function onActivate(env) {
  const names = await env.caches.keys();
  await Promise.all(names.filter((n) => n !== env.CACHE).map((n) => env.caches.delete(n)));
}

export async function onFetch(env, request) {
  const cache = await env.caches.open(env.CACHE);
  const hit = await cache.match(request);
  if (hit) return hit;
  const response = await env.fetch(request);
  await cache.put(request, response.clone());
  return response;
}
