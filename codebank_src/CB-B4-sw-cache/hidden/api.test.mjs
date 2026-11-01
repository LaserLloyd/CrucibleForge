import assert from "node:assert/strict";
import { onFetch } from "../sw-core.mjs";
import { FakeCaches, env, network, req } from "./_fake.mjs";

export async function testNetworkFirstAndRefreshesCache() {
  const caches = new FakeCaches();
  const c = await caches.open("app-v13");
  await c.put(req("/api/items"), new Response("stale"));
  const net = network({ "/api/items": { body: "fresh" } });
  const res = await onFetch(env(caches, net), req("/api/items"));
  assert.equal(await res.text(), "fresh");
  assert.equal(await (await c.match(req("/api/items"))).text(), "fresh");
}

export async function testOfflineFallsBackToCacheThen503() {
  const caches = new FakeCaches();
  const c = await caches.open("app-v13");
  await c.put(req("/api/items"), new Response("cached list"));
  const e = env(caches, network({ "/api/items": "offline", "/api/other": "offline" }));
  assert.equal(await (await onFetch(e, req("/api/items"))).text(), "cached list");
  const res = await onFetch(e, req("/api/other"));
  assert.equal(res.status, 503);
  assert.equal(res.headers.get("Content-Type"), "application/json");
  assert.deepEqual(await res.json(), { error: "offline" });
}

export async function testServerErrorReturnedNotCached() {
  const caches = new FakeCaches();
  const c = await caches.open("app-v13");
  await c.put(req("/api/items"), new Response("good"));
  const res = await onFetch(env(caches, network({ "/api/items": { status: 500, body: "boom" } })), req("/api/items"));
  assert.equal(res.status, 500);
  assert.equal(await (await c.match(req("/api/items"))).text(), "good");
}
