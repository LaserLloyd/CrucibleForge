import assert from "node:assert/strict";
import { onInstall } from "../sw-core.mjs";

export async function testInstallPrecaches() {
  const opened = [];
  const added = [];
  const env = {
    CACHE: "app-v13", PRECACHE: ["/", "/app.js"],
    caches: { open: async (n) => { opened.push(n); return { addAll: async (u) => added.push(...u) }; } },
  };
  await onInstall(env);
  assert.deepEqual([opened, added], [["app-v13"], ["/", "/app.js"]]);
}
