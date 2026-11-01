import assert from "node:assert/strict";
import { onFetch } from "../sw-core.mjs";
import { FakeCaches, env, network, req } from "./_fake.mjs";

export async function testCacheFirst() {
  const caches = new FakeCaches();
  const c = await caches.open("app-v13");
  await c.put(req("/app.js"), new Response("cached js"));
  const net = network({ "/app.js": { body: "new js" } });
  assert.equal(await (await onFetch(env(caches, net), req("/app.js"))).text(), "cached js");
  assert.deepEqual(net.calls, []);
}

export async function testMissFetchesAndStoresOnly200() {
  const caches = new FakeCaches();
  const net = network({ "/a.css": { body: "css" }, "/gone.png": { status: 404 }, "/moved": { status: 301 } });
  const e = env(caches, net);
  assert.equal(await (await onFetch(e, req("/a.css"))).text(), "css");
  assert.equal((await onFetch(e, req("/gone.png"))).status, 404);
  assert.equal((await onFetch(e, req("/moved"))).status, 301);
  const c = await caches.open("app-v13");
  assert.deepEqual(c.puts, ["https://home.local/a.css"]);
}

export async function testOfflineNavigationAndAssets() {
  const caches = new FakeCaches();
  const c = await caches.open("app-v13");
  await c.put(req("/offline.html"), new Response("you are offline"));
  const e = env(caches, network({}));
  const page = await onFetch(e, req("/lists/today", { mode: "navigate" }));
  assert.equal(await page.text(), "you are offline");
  const img = await onFetch(e, req("/img/logo.png"));
  assert.equal(img.status, 504);
  assert.equal(await img.text(), "");
}
