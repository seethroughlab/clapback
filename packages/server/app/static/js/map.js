/* The corpus as a picture — and, on the explorer, as a thing to click.
 *
 * Draws /static/map.json (quantised UMAP coordinates, one point per recording)
 * onto a canvas, reading its colours from the stylesheet's tokens so it follows
 * the reader's theme. `decorate(canvas)` is the landing page's use: paint and
 * stop. `explorer(canvas, opts)` adds hover, click and highlights, and fetches
 * the hash prefixes (/static/map-index.json) only then — the landing page never
 * pays for them.
 */
(function () {
  "use strict";

  const SUBSAMPLE_BELOW = 500; // css px; narrower canvases draw every 3rd point

  function luminance(hex) {
    const m = /^#?([0-9a-f]{6})$/i.exec(hex.trim());
    if (!m) return 0;
    const n = parseInt(m[1], 16);
    return (0.2126 * (n >> 16) + 0.7152 * ((n >> 8) & 255) + 0.0722 * (n & 255)) / 255;
  }

  function tokens(el) {
    const cs = getComputedStyle(el);
    const get = (name, fallback) => (cs.getPropertyValue(name) || fallback).trim();
    const surface2 = get("--surface-2", "#0d0b14");
    return {
      accent: get("--accent", "#a78bfa"),
      surface2,
      text: get("--text", "#ece9f4"),
      ok: get("--ok", "#22c55e"),
      // Additive blending is what makes dense regions glow on a dark ground; on a
      // light ground it only washes them out, so there the dots simply overlap.
      light: luminance(surface2) > 0.5,
    };
  }

  let mapPromise = null;
  function loadMap() {
    if (!mapPromise) {
      mapPromise = fetch("/static/map.json").then((r) => r.json());
    }
    return mapPromise;
  }

  let indexPromise = null;
  function loadIndex() {
    if (!indexPromise) {
      indexPromise = fetch("/static/map-index.json").then((r) => (r.ok ? r.json() : null)).catch(() => null);
    }
    return indexPromise;
  }

  function fit(canvas) {
    const rect = canvas.getBoundingClientRect();
    const dpr = window.devicePixelRatio || 1;
    const size = Math.max(1, Math.round(rect.width));
    canvas.width = Math.round(size * dpr);
    canvas.height = Math.round(size * dpr);
    return { size, dpr };
  }

  function draw(canvas, data, state) {
    const { size, dpr } = fit(canvas);
    const ctx = canvas.getContext("2d");
    const t = tokens(canvas);
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.fillStyle = t.surface2;
    ctx.fillRect(0, 0, size, size);
    const grid = data.grid || 1000;
    const scale = size / grid;
    const xy = data.xy;
    const named = data.named || null; // optional bit array, one per point
    const step = size < SUBSAMPLE_BELOW ? 3 : 1;
    const r = Math.max(1, size / 700);
    ctx.globalCompositeOperation = t.light ? "source-over" : "lighter";
    ctx.fillStyle = t.accent;
    const alphaNamed = t.light ? 0.6 : 0.55, alphaPlain = t.light ? 0.3 : 0.28;
    for (let i = 0, n = xy.length / 2; i < n; i += step) {
      const x = xy[2 * i] * scale;
      const y = (grid - xy[2 * i + 1]) * scale;
      ctx.globalAlpha = named && named[i] ? alphaNamed : alphaPlain;
      ctx.beginPath();
      ctx.arc(x, y, r, 0, Math.PI * 2);
      ctx.fill();
    }
    ctx.globalCompositeOperation = "source-over";
    ctx.globalAlpha = 1;
    if (state) {
      for (const i of state.neighbours || []) {
        const x = xy[2 * i] * scale, y = (grid - xy[2 * i + 1]) * scale;
        ctx.strokeStyle = t.text; ctx.lineWidth = 1.2; ctx.globalAlpha = 0.7;
        ctx.beginPath(); ctx.arc(x, y, 6, 0, Math.PI * 2); ctx.stroke();
        if (state.selected != null) {
          const sx = xy[2 * state.selected] * scale, sy = (grid - xy[2 * state.selected + 1]) * scale;
          ctx.globalAlpha = 0.25; ctx.beginPath(); ctx.moveTo(sx, sy); ctx.lineTo(x, y); ctx.stroke();
        }
      }
      ctx.globalAlpha = 1;
      if (state.hover != null && state.hover !== state.selected) {
        const x = xy[2 * state.hover] * scale, y = (grid - xy[2 * state.hover + 1]) * scale;
        ctx.strokeStyle = t.text; ctx.lineWidth = 1.5;
        ctx.beginPath(); ctx.arc(x, y, 7, 0, Math.PI * 2); ctx.stroke();
      }
      if (state.selected != null) {
        const x = xy[2 * state.selected] * scale, y = (grid - xy[2 * state.selected + 1]) * scale;
        ctx.strokeStyle = t.ok; ctx.lineWidth = 2.5;
        ctx.beginPath(); ctx.arc(x, y, 10, 0, Math.PI * 2); ctx.stroke();
        ctx.fillStyle = t.ok;
        ctx.beginPath(); ctx.arc(x, y, 3.5, 0, Math.PI * 2); ctx.fill();
      }
    }
    return { size, scale, grid };
  }

  function onThemeChange(fn) {
    const mq = window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)");
    if (mq && mq.addEventListener) mq.addEventListener("change", fn);
  }

  /** Paint once, repaint on resize and theme change. */
  function decorate(canvas) {
    if (!canvas) return;
    loadMap().then((data) => {
      const paint = () => draw(canvas, data, null);
      paint();
      window.addEventListener("resize", paint);
      onThemeChange(paint);
    });
  }

  /** A spatial grid over the points so hover is a bucket lookup, not a scan. */
  function buildBuckets(data, cells) {
    const grid = data.grid || 1000;
    const xy = data.xy;
    const buckets = new Map();
    const cell = grid / cells;
    for (let i = 0, n = xy.length / 2; i < n; i++) {
      const bx = Math.floor(xy[2 * i] / cell), by = Math.floor((grid - xy[2 * i + 1]) / cell);
      const k = bx * cells + by;
      let b = buckets.get(k);
      if (!b) buckets.set(k, (b = []));
      b.push(i);
    }
    return {
      nearest(x, y, maxDist) {
        const bx = Math.floor(x / cell), by = Math.floor(y / cell);
        let best = -1, bestD = maxDist * maxDist;
        for (let dx = -1; dx <= 1; dx++) for (let dy = -1; dy <= 1; dy++) {
          const b = buckets.get((bx + dx) * cells + (by + dy));
          if (!b) continue;
          for (const i of b) {
            const px = xy[2 * i], py = grid - xy[2 * i + 1];
            const d = (px - x) * (px - x) + (py - y) * (py - y);
            if (d < bestD) { bestD = d; best = i; }
          }
        }
        return best;
      },
    };
  }

  /**
   * The explorer. opts.onSelect(index, prefix) is called with the point's hash
   * prefix; the page resolves it. Returns {select(index), highlight(indices), indexOf(prefix)}.
   */
  function explorer(canvas, opts) {
    const state = { hover: null, selected: null, neighbours: [] };
    let data = null, index = null, buckets = null, geom = null;
    const api = {
      select(i) { state.selected = i; state.neighbours = []; repaint(); },
      highlight(indices) { state.neighbours = indices; repaint(); },
      indexOf(prefix) {
        if (!index) return -1;
        const p = prefix.slice(0, 12);
        return index.indexOf(p);
      },
      prefixOf(i) { return index ? index[i] : null; },
      isNamed(i) { return !!(data && data.named && data.named[i]); },
      randomNamed() {
        if (!data || !data.named) return -1;
        const n = data.xy.length / 2;
        for (let tries = 0; tries < 200; tries++) {
          const i = Math.floor(Math.random() * n);
          if (data.named[i]) return i;
        }
        return -1;
      },
    };
    function repaint() { if (data) geom = draw(canvas, data, state); }
    Promise.all([loadMap(), loadIndex()]).then(([m, idx]) => {
      data = m; index = idx;
      buckets = buildBuckets(data, 40);
      repaint();
      window.addEventListener("resize", repaint);
      onThemeChange(repaint);
      if (opts && opts.onReady) opts.onReady(api, !!index);
    });
    function pointAt(ev) {
      if (!geom || !buckets) return -1;
      const rect = canvas.getBoundingClientRect();
      const x = (ev.clientX - rect.left) / geom.scale, y = (ev.clientY - rect.top) / geom.scale;
      return buckets.nearest(x, y, 14 / geom.scale);
    }
    canvas.addEventListener("mousemove", (ev) => {
      const i = pointAt(ev);
      if (i !== state.hover) { state.hover = i < 0 ? null : i; canvas.style.cursor = i < 0 ? "default" : "pointer"; repaint(); }
    });
    canvas.addEventListener("mouseleave", () => { state.hover = null; repaint(); });
    canvas.addEventListener("click", (ev) => {
      const i = pointAt(ev);
      if (i < 0 || !opts || !opts.onSelect) return;
      api.select(i);
      opts.onSelect(i, api.prefixOf(i));
    });
    return api;
  }

  window.clapbackMap = { decorate, explorer };
})();
