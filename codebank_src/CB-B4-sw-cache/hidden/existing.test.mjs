import assert from "node:assert/strict";
import { onInstall, onActivate } from "../sw-core.mjs";
import { FakeCaches, env, network, req } from "./_fake.mjs";

export async function testInstall() {
  const caches = new FakeCaches();
  await onInstall(env(caches, network({})));
  const c = await caches.open("app-v13");
  assert.equal(await (await c.match(req("/offline.html"))).text(), "pre:/offline.html");
}

export async function testActivateKeepsCurrent() {
  const caches = new FakeCaches(["app-v13"]);
  await onActivate(env(caches, network({})));
  assert.deepEqual(await caches.keys(), ["app-v13"]);
}
