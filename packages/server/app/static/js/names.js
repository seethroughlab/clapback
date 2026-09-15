/* Names are the visitor's browser's business.
 *
 * The commons holds no titles — a row is a hash, and at most a MusicBrainz
 * recording id somebody claimed for it. Turning that id into "Plaid — Zala"
 * means asking MusicBrainz, and this site's server never does (`ADR-0012`).
 * So the page asks, from here: one request a second, which is MusicBrainz's
 * limit per address, answers cached in localStorage so a repeat is free, and
 * a placeholder shown honestly while the queue drains.
 */
(function () {
  "use strict";

  const SPACING_MS = 1100;
  const CACHE_KEY = "clapback:names";
  const MB = "https://musicbrainz.org/ws/2/recording/";

  let cache = {};
  try { cache = JSON.parse(localStorage.getItem(CACHE_KEY) || "{}") || {}; } catch (e) { cache = {}; }
  function remember(mbid, name) {
    cache[mbid] = name;
    try { localStorage.setItem(CACHE_KEY, JSON.stringify(cache)); } catch (e) { /* private mode: fine */ }
  }

  const queue = [];
  const waiting = new Map(); // mbid -> [callbacks]
  let timer = null;

  function pump() {
    if (timer || queue.length === 0) return;
    const mbid = queue.shift();
    timer = setTimeout(() => { timer = null; pump(); }, SPACING_MS);
    fetch(MB + encodeURIComponent(mbid) + "?fmt=json&inc=artists", { headers: { Accept: "application/json" } })
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => {
        let name = null;
        if (d && d.title) {
          const artist = (d["artist-credit"] || []).map((c) => (c.name || "") + (c.joinphrase || "")).join("");
          name = artist ? artist + " — " + d.title : d.title;
          remember(mbid, name);
        }
        (waiting.get(mbid) || []).forEach((cb) => cb(name));
        waiting.delete(mbid);
      })
      .catch(() => {
        (waiting.get(mbid) || []).forEach((cb) => cb(null));
        waiting.delete(mbid);
      });
  }

  /** resolve(mbid, cb): cb(name|null). Cached answers call back synchronously. */
  function resolve(mbid, cb) {
    if (!mbid) { cb(null); return; }
    if (cache[mbid]) { cb(cache[mbid]); return; }
    if (waiting.has(mbid)) { waiting.get(mbid).push(cb); return; }
    waiting.set(mbid, [cb]);
    queue.push(mbid);
    pump();
  }

  /** Fill an element: "resolving…" now, the name when it arrives, the id if it never does. */
  function fill(el, mbid) {
    if (!mbid) { el.textContent = "not yet named by anyone"; el.className = "name name--unnamed"; return; }
    el.textContent = "resolving…";
    el.className = "name name--pending";
    resolve(mbid, (name) => {
      el.className = "name";
      el.textContent = name || mbid;
    });
  }

  window.clapbackNames = { resolve, fill };
})();
