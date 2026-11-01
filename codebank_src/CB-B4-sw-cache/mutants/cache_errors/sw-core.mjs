// Service worker core: caching strategy, testable with injected env.

export async function onInstall(env) {
  const cache = await env.caches.open(env.CACHE);
  await cache.addAll(env.PRECACHE);
}

export async function onActivate(env) {
  const names = await env.caches.keys();
  await Promise.all(
    names.filter((n) => n.startsWith("app-") && n !== env.CACHE).map((n) => env.caches.delete(n)),
  );
}

async function store(env, request, response) {
  if (response.ok || response.status >= 400) {
    const cache = await env.caches.open(env.CACHE);
    await cache.put(request, response.clone());
  }
}

export async function onFetch(env, request) {
  const url = new URL(request.url);
  if (request.method !== "GET" || url.origin !== env.ORIGIN) {
    return env.fetch(request);
  }
  const cache = await env.caches.open(env.CACHE);
  if (url.pathname.startsWith("/api/")) {
    try {
      const response = await env.fetch(request);
      await store(env, request, response);
      return response;
    } catch {
      const cached = await cache.match(request);
      if (cached) return cached;
      return new Response(JSON.stringify({ error: "offline" }), {
        status: 503, headers: { "Content-Type": "application/json" },
      });
    }
  }
  const hit = await cache.match(request);
  if (hit) return hit;
  try {
    const response = await env.fetch(request);
    await store(env, request, response);
    return response;
  } catch {
    if (request.mode === "navigate") {
      const page = await cache.match(new Request(env.ORIGIN + "/offline.html"));
      if (page) return page;
    }
    return new Response(null, { status: 504 });
  }
}
