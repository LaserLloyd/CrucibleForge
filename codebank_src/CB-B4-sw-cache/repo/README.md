# hearth web app: service worker core

`sw.js` (not shown) is a thin shell that forwards the worker events to
`sw-core.mjs`, passing an `env` = `{caches, fetch, CACHE, ORIGIN, PRECACHE}`
(`CACHE` is this release's cache name, e.g. `app-v13`). The core never touches
globals, so it is tested in node with fakes. `Request`/`Response` are the
standard Fetch API classes.

Caching rules (the contract):

1. `onInstall(env)`: open `CACHE` and `addAll(PRECACHE)`.
2. `onActivate(env)`: delete every cache whose name starts with `app-` other
   than `CACHE`. Caches with other names (e.g. `media-v1`, owned by the photo
   viewer) are never touched.
3. `onFetch(env, request)` returns the Response to use:
   * Requests that are not GET, and GETs to another origin, go to the
     network untouched and are never cached.
   * Same-origin GETs under `/api/` are network-first: answer with the
     network response; if it is a 200, also store a copy in `CACHE`. If the
     network fails (fetch rejects), answer with the cached copy if there is
     one, else with status 503, `Content-Type: application/json`, body
     `{"error":"offline"}`.
   * Other same-origin GETs are cache-first: a cached copy wins; otherwise
     fetch, store a copy in `CACHE` if the status is 200, and answer with the
     network response. If the network fails: a navigation request
     (`request.mode === "navigate"`) gets the cached `/offline.html`, anything
     else gets status 504 with an empty body.
   * Only status-200 responses are ever stored.
