import assert from "node:assert/strict";
import { onActivate } from "../sw-core.mjs";
import { FakeCaches, env, network } from "./_fake.mjs";

export async function testOnlyOldAppCachesDeleted() {
  const caches = new FakeCaches(["app-v11", "app-v12", "app-v13", "media-v1", "thumbs", "app"]);
  await onActivate(env(caches, network({})));
  assert.deepEqual((await caches.keys()).sort(), ["app", "app-v13", "media-v1", "thumbs"]);
}
