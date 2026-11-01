import assert from "node:assert/strict";
import { onFetch } from "../sw-core.mjs";
import { FakeCaches, env, network, req } from "./_fake.mjs";

export async function testPostGoesToNetworkUncached() {
  const caches = new FakeCaches();
  const net = network({ "/api/items": { status: 201, body: "created" } });
  const res = await onFetch(env(caches, net), req("/api/items", { method: "POST" }));
  assert.equal(res.status, 201);
  assert.deepEqual(net.calls, ["POST https://home.local/api/items"]);
  assert.deepEqual((await caches.open("app-v13")).puts, []);
}

export async function testOfflinePostRejects() {
  const caches = new FakeCaches();
  await assert.rejects(onFetch(env(caches, network({})), req("/api/items", { method: "POST" })));
}

export async function testCrossOriginNotCached() {
  const caches = new FakeCaches();
  const c = await caches.open("app-v13");
  const url = "https://cdn.example.org/font.woff2";
  const net = network({ [url]: { body: "font" } });
  const e = env(caches, net);
  assert.equal(await (await onFetch(e, req(url))).text(), "font");
  assert.equal(await (await onFetch(e, req(url))).text(), "font");
  assert.equal(net.calls.length, 2);
  assert.deepEqual(c.puts, []);
}
