// Test fakes: a CacheStorage keyed by URL and a scripted network.
export const ORIGIN = "https://home.local";

export function req(path, { method = "GET", mode = "cors", origin = ORIGIN } = {}) {
  return { url: path.startsWith("http") ? path : origin + path, method, mode };
}

class FakeCache {
  constructor() { this.map = new Map(); this.puts = []; }
  async match(r) {
    const url = new URL(typeof r === "string" ? r : r.url, ORIGIN).href;
    const hit = this.map.get(url);
    return hit ? hit.clone() : undefined;
  }
  async put(r, res) {
    if (typeof r !== "string" && (r.method || "GET") !== "GET") throw new TypeError("Cache.put: only GET requests");
    const url = new URL(typeof r === "string" ? r : r.url, ORIGIN).href;
    this.puts.push(url);
    this.map.set(url, res.clone());
  }
  async addAll(urls) { for (const u of urls) this.map.set(ORIGIN + u, new Response("pre:" + u)); }
}

export class FakeCaches {
  constructor(names = []) { this.stores = new Map(names.map((n) => [n, new FakeCache()])); }
  async open(n) { if (!this.stores.has(n)) this.stores.set(n, new FakeCache()); return this.stores.get(n); }
  async keys() { return [...this.stores.keys()]; }
  async delete(n) { return this.stores.delete(n); }
  async has(n) { return this.stores.has(n); }
  async match(r) {
    for (const c of this.stores.values()) { const hit = await c.match(r); if (hit) return hit; }
    return undefined;
  }
}

export function network(routes) {
  const calls = [];
  const fetch = async (r) => {
    calls.push(`${r.method} ${r.url}`);
    const h = routes[r.url] ?? routes[new URL(r.url).pathname];
    if (h === undefined || h === "offline") throw new TypeError("Failed to fetch");
    return typeof h === "function" ? h() : new Response(h.body ?? "", { status: h.status ?? 200 });
  };
  return { fetch, calls };
}

export function env(caches, net) {
  return { caches, fetch: net.fetch, CACHE: "app-v13", ORIGIN, PRECACHE: ["/", "/offline.html"] };
}
