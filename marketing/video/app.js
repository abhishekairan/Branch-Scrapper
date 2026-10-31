/* Ghost Scraper v0.1.0 promo: deterministic, time-driven.
   window.renderAt(t) sets every element's state for time t (seconds). No CSS transitions/animations. */
(() => {
  'use strict';

  const q = new URLSearchParams(location.search);
  const FORMAT = q.get('format') === '4x5' ? '4x5' : '9x16';
  const W = 1080;
  const H = FORMAT === '4x5' ? 1350 : 1920;
  const SAFE = FORMAT === '4x5' ? { t: 90, b: 100 } : { t: 230, b: 330 }; // keep key content inside
  const SAFE_H = H - SAFE.t - SAFE.b;
  const HS = FORMAT === '4x5' ? 92 : 104;
  const HXL = FORMAT === '4x5' ? 128 : 148;
  const root = document.documentElement.style;
  root.setProperty('--W', W + 'px');
  root.setProperty('--H', H + 'px');
  root.setProperty('--hs', HS + 'px');
  root.setProperty('--hxl', HXL + 'px');

  const stage = document.getElementById('stage');
  const edge = document.getElementById('edge');

  // ---------- assets ----------
  const SHOTS = '../../docs/screenshots/';
  const SRC = {
    run: SHOTS + 'run.png',
    grid: SHOTS + 'browsers-grid.png',
    dark: SHOTS + 'data-dark.png',
    light: SHOTS + 'data-light.png',
    icon: '../../app/src-tauri/icons/source/icon.svg',
  };
  const imgs = [];

  // ---------- math / easing ----------
  const clamp = (x, a = 0, b = 1) => Math.min(b, Math.max(a, x));
  const lerp = (a, b, k) => a + (b - a) * k;
  const pr = (t, s, d) => clamp((t - s) / d);
  const E = {
    inCubic: (x) => x * x * x,
    outCubic: (x) => 1 - Math.pow(1 - x, 3),
    inOutCubic: (x) => (x < 0.5 ? 4 * x * x * x : 1 - Math.pow(-2 * x + 2, 3) / 2),
    inOutQuart: (x) => (x < 0.5 ? 8 * x * x * x * x : 1 - Math.pow(-2 * x + 2, 4) / 2),
    outExpo: (x) => (x >= 1 ? 1 : 1 - Math.pow(2, -10 * x)),
    outQuint: (x) => 1 - Math.pow(1 - x, 5),
    outBack: (x) => {
      const c1 = 1.70158, c3 = c1 + 1;
      return 1 + c3 * Math.pow(x - 1, 3) + c1 * Math.pow(x - 1, 2);
    },
  };
  const hash = (n) => {
    const s = Math.sin(n * 127.1 + 311.7) * 43758.5453;
    return s - Math.floor(s);
  };

  // ---------- dom helpers ----------
  function el(tag, cls, parent, html) {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (html != null) e.innerHTML = html;
    if (parent) parent.appendChild(e);
    return e;
  }
  function img(src, cls, parent) {
    const i = document.createElement('img');
    i.className = cls || '';
    i.decoding = 'sync';
    i.src = src;
    if (parent) parent.appendChild(i);
    imgs.push(i);
    return i;
  }
  const px = (n) => n + 'px';

  // ---------- layout shared by screenshot scenes ----------
  const CAP_H = FORMAT === '4x5' ? 290 : 330;
  const FRAME_H = FORMAT === '4x5' ? 770 : 860;
  const FRAME_W = 960;
  const GAP = 36;
  const BLOCK = CAP_H + GAP + FRAME_H;
  const CAP_TOP = SAFE.t + (SAFE_H - BLOCK) / 2;
  const FRAME_TOP = CAP_TOP + CAP_H + GAP;

  // ---------- timeline ----------
  const T = [0, 2.4, 4.6, 6.8, 9.6, 11.8, 14.4, 16.4, 18.2, 21.8];
  const TOTAL = T[T.length - 1];
  const FPS = 30;
  const D = 0.42; // transition duration
  const TRANS = [
    null,
    { type: 'curtainUp' },
    { type: 'pushLeft' },
    { type: 'iris', x: W * 0.5, y: H * 0.58 },
    { type: 'curtainLeft' },
    { type: 'zoom' },
    { type: 'pushUp' },
    { type: 'curtainRight' },
    { type: 'iris', x: W * 0.5, y: H * 0.45 },
  ];

  // ---------- background fx ----------
  function makeBg(parent, seed) {
    const bg = el('div', 'bgfx', parent);
    const grid = el('div', 'grid', bg);
    const g1 = el('div', 'glow g1', bg);
    const g2 = el('div', 'glow g2', bg);
    el('div', 'vig', bg);
    return {
      update(t) {
        grid.style.transform = `translateY(${(t * 26) % 90}px)`;
        const a = t * 0.55 + seed * 1.7;
        g1.style.left = px(W * (0.62 + 0.22 * Math.cos(seed)) - 650 + 90 * Math.sin(a));
        g1.style.top = px(H * (0.20 + 0.45 * hash(seed + 3)) - 650 + 70 * Math.cos(a * 0.8));
        g2.style.left = px(W * (0.15 + 0.2 * hash(seed + 9)) - 700 + 80 * Math.cos(a * 0.9));
        g2.style.top = px(H * (0.78 - 0.25 * hash(seed + 5)) - 700 + 90 * Math.sin(a * 0.7));
      },
    };
  }

  // ---------- caption: masked line reveals ----------
  // beats: [{in, out?, lines:[{t, c?, at?}]}] ; times are scene-local seconds
  function Caption(parent, top, beats) {
    const rootEl = el('div', 'cap', parent);
    rootEl.style.top = px(top);
    const bs = beats.map((b) => {
      const be = el('div', 'beat', rootEl);
      const lines = b.lines.map((l) => {
        const ln = el('div', 'ln ' + (l.c || 'h'), be);
        const sp = el('span', '', ln, l.t);
        return { ln, sp, l };
      });
      return { b, be, lines };
    });
    return {
      root: rootEl,
      update(lt) {
        for (const x of bs) {
          const b = x.b;
          x.lines.forEach((o, i) => {
            const at = o.l.at != null ? o.l.at : b.in + i * 0.09;
            const pi = E.outExpo(pr(lt, at, 0.55));
            const po = b.out == null ? 0 : E.inCubic(pr(lt, b.out + i * 0.05, 0.2));
            const y = (1 - pi) * 118 - po * 118;
            o.sp.style.transform = `translateY(${y}%) rotate(${(1 - pi) * 4}deg)`;
          });
        }
      },
    };
  }

  // ---------- screenshot frame with camera + highlights ----------
  function camAt(kfs, t) {
    if (t <= kfs[0].t) return kfs[0];
    for (let i = 0; i < kfs.length - 1; i++) {
      const a = kfs[i], b = kfs[i + 1];
      if (t <= b.t) {
        const k = E.inOutQuart(pr(t, a.t, b.t - a.t));
        return { cx: lerp(a.cx, b.cx, k), cy: lerp(a.cy, b.cy, k), s: a.s * Math.pow(b.s / a.s, k) };
      }
    }
    return kfs[kfs.length - 1];
  }
  function place(c, fw, fh) {
    const minS = Math.max(fw / 1440, fh / 900);
    const s = Math.max(minS, c.s);
    const cx = clamp(c.cx, fw / (2 * s), 1440 - fw / (2 * s));
    const cy = clamp(c.cy, fh / (2 * s), 900 - fh / (2 * s));
    return { s, tx: fw / 2 - cx * s, ty: fh / 2 - cy * s };
  }

  let READY = false;

  function Shot(parent, o) {
    const fw = FRAME_W, fh0 = o.fh || FRAME_H;
    const rootEl = el('div', 'frame', parent);
    rootEl.style.left = px((W - fw) / 2);
    rootEl.style.width = px(fw);
    const im = img(o.src, 'shot', rootEl);
    const im2 = o.src2 ? img(o.src2, 'shot', rootEl) : null;
    const scrim = el('div', 'scrim', rootEl);
    const ovl = el('div', 'ovl', rootEl);
    const boxes = (o.boxes || []).map((b) => {
      const ring = el('div', 'hl hlr', ovl);
      const e = el('div', 'hl', ovl);
      const lab = b.label ? { el: el('div', 'hlab', ovl, b.label), w: 0, h: 0 } : null;
      return { b, e, ring, lab };
    });
    const api = {
      root: rootEl, ovl, scrim, fw, fh: fh0, pl: null,
      update(lt) {
        const fh = o.fhAt ? o.fhAt(lt) : fh0; // frame height may animate (centered in the frame zone)
        rootEl.style.top = px(FRAME_TOP + (FRAME_H - fh) / 2);
        rootEl.style.height = px(fh);
        const enter = o.enter != null ? o.enter : 0.08;
        const p = E.outExpo(pr(lt, enter, 0.7));
        rootEl.style.transform = `translateY(${(1 - p) * 150}px) scale(${0.92 + 0.08 * p})`;
        rootEl.style.opacity = clamp(p * 3);
        const c = camAt(o.cams, lt);
        const pl = place(c, fw, fh);
        api.pl = pl;
        const tf = `translate(${pl.tx}px, ${pl.ty}px) scale(${pl.s})`;
        im.style.transform = tf;
        if (im2) {
          im2.style.transform = tf;
          const f = o.flip ? E.inOutCubic(pr(lt, o.flip.t, 0.6)) : 0;
          im2.style.clipPath = f <= 0 ? 'circle(0px at 0 0)' : `circle(${f * 1800}px at ${o.flip.x}px ${o.flip.y}px)`;
        }
        for (const bx of boxes) {
          const b = bx.b;
          const pin = E.outCubic(pr(lt, b.t0, 0.28));
          const pout = b.t1 != null ? E.inCubic(pr(lt, b.t1, 0.2)) : 0;
          const a = pin * (1 - pout);
          if (a <= 0.001) {
            bx.e.style.display = 'none';
            bx.ring.style.display = 'none';
            if (bx.lab) bx.lab.el.style.display = 'none';
            continue;
          }
          const pad = 9;
          const x = b.x * pl.s + pl.tx - pad, y = b.y * pl.s + pl.ty - pad;
          const w = b.w * pl.s + pad * 2, h = b.h * pl.s + pad * 2;
          const grow = 1 + (1 - pin) * 0.12;
          bx.e.style.display = 'block';
          bx.e.style.left = px(x - (w * (grow - 1)) / 2);
          bx.e.style.top = px(y - (h * (grow - 1)) / 2);
          bx.e.style.width = px(w * grow);
          bx.e.style.height = px(h * grow);
          bx.e.style.opacity = a;
          const k = ((lt - b.t0) * 1.15) % 1;
          const infl = k * 34;
          bx.ring.style.display = 'block';
          bx.ring.style.left = px(x - infl);
          bx.ring.style.top = px(y - infl);
          bx.ring.style.width = px(w + infl * 2);
          bx.ring.style.height = px(h + infl * 2);
          bx.ring.style.opacity = a * (1 - k) * 0.7;
          if (bx.lab) {
            const L = bx.lab;
            L.el.style.display = 'block';
            if (READY && !L.w) { L.w = L.el.offsetWidth; L.h = L.el.offsetHeight; }
            const lw = L.w || 300, lh = L.h || 72;
            const lx = clamp(x, 16, fw - lw - 16);
            let ly = y - lh - 16;
            if (ly < 16) ly = y + h + 16;
            ly = clamp(ly, 16, fh - lh - 16);
            L.el.style.left = px(lx);
            L.el.style.top = px(ly + (1 - pin) * 16);
            L.el.style.opacity = a;
          }
        }
        return pl;
      },
    };
    return api;
  }

  // ---------- scene scaffold ----------
  const scenes = [];
  function makeScene(i) {
    const rootEl = el('div', 'scene', stage);
    rootEl.style.zIndex = i + 1;
    const bg = makeBg(rootEl, i + 1);
    const s = { i, root: rootEl, bg, update() {} };
    scenes.push(s);
    return s;
  }
  const mid = (h) => SAFE.t + (SAFE_H - h) / 2;

  // ============ S1: hook ============
  (function () {
    const s = makeScene(0);
    const skel = el('div', 'skel', s.root);
    const ROW = 170;
    const N = Math.ceil(H / ROW) + 2;
    const rows = [];
    for (let k = 0; k < N; k++) {
      const r = el('div', 'row', skel);
      el('div', 'av', r);
      const b1 = el('div', 'b1', r);
      const b2 = el('div', 'b2', r);
      const b3 = el('div', 'b3', r);
      b1.style.width = px(220 + hash(k + 1) * 520);
      b2.style.width = px(560 + hash(k + 7) * 260);
      b3.style.width = px(300 + hash(k + 13) * 360);
      if (k % 4 === 1) { b1.style.background = 'var(--accent)'; }
      rows.push(r);
    }
    const top = mid(HXL * 0.96 * 2) - 30;
    const cap = Caption(s.root, top, [
      { in: 0.05, out: 1.12, lines: [{ t: 'Finding great', c: 'xl' }, { t: 'GitHub <em>repos?</em>', c: 'xl' }] },
      {
        in: 1.2,
        lines: [
          { t: 'Stop <span class="sk" id="sk">scrolling</span>', c: 'xl' },
          { t: '<em>search pages.</em>', c: 'xl' },
        ],
      },
    ]);
    const sk = cap.root.querySelector('#sk');
    s.update = (lt) => {
      const V = 1500;
      let sc;
      if (lt < 1.1) sc = V * lt;
      else { const u = pr(lt, 1.1, 0.32); sc = V * 1.1 + V * 0.32 * (u - (u * u) / 2); }
      const span = N * ROW;
      rows.forEach((r, k) => {
        let y = (((k * ROW - sc) % span) + span) % span - ROW;
        r.style.transform = `translateY(${y}px)`;
      });
      const dim = lerp(0.2, 0.08, E.outCubic(pr(lt, 1.1, 0.4)));
      skel.style.opacity = dim * clamp(lt * 4 + 0.2);
      const k = E.outExpo(pr(lt, 1.62, 0.28));
      sk.style.setProperty('--sk', k);
      sk.style.opacity = lerp(1, 0.5, E.outCubic(pr(lt, 1.7, 0.3)));
      cap.update(lt);
    };
  })();

  // ============ S2: reveal ============
  (function () {
    const s = makeScene(1);
    const IC = FORMAT === '4x5' ? 290 : 380;
    const total = IC + 44 + 145 + 28 + 2 * 66;
    const top = mid(total);
    const cx = 60 + IC / 2, cy = top + IC / 2;
    const glow = el('div', 'glow', s.root);
    glow.style.cssText = `position:absolute;border-radius:50%;width:${IC * 3}px;height:${IC * 3}px;left:${cx - IC * 1.5}px;top:${cy - IC * 1.5}px;background:radial-gradient(circle, rgba(79,191,179,.35), transparent 62%)`;
    const rings = [0, 1, 2].map(() => {
      const r = el('div', 'ring', s.root);
      r.style.width = r.style.height = px(IC);
      r.style.left = px(cx - IC / 2);
      r.style.top = px(cy - IC / 2);
      return r;
    });
    const icon = el('div', 'icon', s.root);
    icon.style.cssText += `left:${60}px;top:${top}px;width:${IC}px;height:${IC}px;filter:drop-shadow(0 30px 50px rgba(0,0,0,.55))`;
    img(SRC.icon, '', icon);
    const chip = el('div', 'chip', s.root, 'v0.1.0');
    chip.style.left = px(60 + IC + 40);
    chip.style.top = px(cy - 36);
    const cap = Caption(s.root, top + IC + 44, [
      {
        in: 0.5,
        lines: [
          { t: 'Ghost <em>Scraper</em>', c: 'wm' },
          { t: 'Search, scrape &amp;', c: 's' },
          { t: 'explore GitHub repos.', c: 's' },
        ],
      },
    ]);
    s.update = (lt) => {
      const p = pr(lt, 0.1, 0.7);
      const sc = lerp(0.15, 1, E.outBack(p));
      icon.style.transform = `scale(${sc}) rotate(${(1 - E.outExpo(p)) * -16}deg)`;
      icon.style.opacity = clamp(p * 5);
      rings.forEach((r, i) => {
        const k = pr(lt, 0.22 + i * 0.16, 1.0);
        r.style.opacity = k > 0 && k < 1 ? (1 - k) * 0.8 : 0;
        r.style.transform = `scale(${1 + E.outCubic(k) * 1.6})`;
      });
      glow.style.opacity = E.outCubic(pr(lt, 0.1, 0.8));
      const cp = E.outBack(pr(lt, 0.85, 0.4));
      chip.style.transform = `scale(${cp}) translateX(${(1 - cp) * -40}px)`;
      chip.style.opacity = clamp(cp * 2);
      cap.update(lt);
    };
  })();

  // ============ S3: run screen ============
  (function () {
    const s = makeScene(2);
    const cap = Caption(s.root, CAP_TOP, [
      { in: 0.12, lines: [{ t: 'Queries + <em>filters.</em>', c: 'h' }, { t: 'Validated before you start.', c: 's' }] },
    ]);
    const shot = Shot(s.root, {
      src: SRC.run,
      cams: [
        { t: 0, cx: 700, cy: 330, s: 1.0 },
        { t: 0.55, cx: 467, cy: 200, s: 1.75 },
        { t: 1.05, cx: 467, cy: 200, s: 1.75 },
        { t: 1.6, cx: 1090, cy: 215, s: 1.5 },
        { t: 9, cx: 1090, cy: 215, s: 1.5 },
      ],
      boxes: [
        { x: 211, y: 128, w: 513, h: 100, t0: 0.6, t1: 1.15, label: 'Your queries' },
        { x: 1090, y: 172, w: 325, h: 80, t0: 1.6, label: 'Exact query sent' },
      ],
    });
    s.update = (lt) => { cap.update(lt); shot.update(lt); };
  })();

  // ============ S4: browsers ============
  (function () {
    const s = makeScene(3);
    const cap = Caption(s.root, CAP_TOP, [
      { in: 0.12, out: 1.38, lines: [{ t: 'Watch every', c: 'h' }, { t: 'browser. <em>Live.</em>', c: 'h' }] },
      { in: 1.62, lines: [{ t: 'Streams only', c: 'h' }, { t: '<em>what you see.</em>', c: 'h' }] },
    ]);
    const tiles = [197, 611, 1026];
    const shot = Shot(s.root, {
      src: SRC.grid,
      // ultra-wide strip so all three tiles fit side by side, then grows tall for the zoom on the status text
      fhAt: (lt) => lerp(450, FRAME_H, E.inOutQuart(pr(lt, 1.35, 0.5))),
      cams: [
        { t: 0, cx: 812, cy: 310, s: 0.74 },
        { t: 1.35, cx: 812, cy: 310, s: 0.76 },
        { t: 1.95, cx: 1230, cy: 150, s: 2.2 },
        { t: 9, cx: 1230, cy: 150, s: 2.2 },
      ],
      boxes: [
        ...tiles.map((x, i) => ({ x, y: 95, w: 401, h: 332, t0: 0.3 + i * 0.22, t1: 1.3 })),
        { x: 1128, y: 58, w: 302, h: 20, t0: 2.05, label: 'Only what is visible' },
      ],
    });
    const live = el('div', 'chip', shot.ovl, '<span class="dot"></span>LIVE');
    live.style.left = '24px';
    live.style.top = '24px';
    live.style.color = 'var(--text)';
    live.style.borderColor = 'rgba(239,125,117,.8)';
    live.style.background = 'rgba(16,19,24,.85)';
    s.update = (lt) => {
      cap.update(lt);
      shot.update(lt);
      const a = E.outBack(pr(lt, 0.3, 0.3)) * (1 - E.inCubic(pr(lt, 1.35, 0.2)));
      live.style.opacity = clamp(a);
      live.style.transform = `scale(${a > 0 ? lerp(0.6, 1, clamp(a)) : 0})`;
      live.querySelector('.dot').style.opacity = 0.55 + 0.45 * Math.sin(lt * 9) ** 2;
    };
  })();

  // ============ S5: shared Chrome RAM ============
  (function () {
    const s = makeScene(4);
    const cap = Caption(s.root, CAP_TOP, [
      { in: 0.1, lines: [{ t: '3 workers.', c: 'h' }, { t: '<em>One Chrome.</em>', c: 'h' }, { t: 'A third less RAM', c: 's' }] },
    ]);
    const chart = el('div', 'chart', s.root);
    const chartH = 640;
    chart.style.top = px(FRAME_TOP + (FRAME_H - chartH) / 2);
    chart.style.height = px(chartH);
    const SC = 960 / 940;
    function row(top, label, segs, color, txt, numColor, grid) {
      const r = el('div', 'crow', chart);
      r.style.top = px(top);
      el('div', 'clabel', r, label);
      const num = el('div', 'cnum', r, '0 MB');
      num.style.color = numColor;
      const bar = el('div', 'cbar', r);
      bar.style.top = px(76);
      bar.style.width = px(960);
      let x = 0;
      segs.forEach((mb, i) => {
        const sg = el('div', 'seg', bar, 'W' + (i + 1));
        const w = mb * SC - 6;
        sg.style.left = px(x);
        sg.style.width = px(w);
        sg.style.background = color;
        sg.style.color = txt;
        x += mb * SC;
      });
      return { r, num, bar };
    }
    const r1 = row(30, 'Shared · 1 Chrome', [320, 155, 155], 'var(--accent)', 'var(--on-accent)', 'var(--accent)');
    const r2 = row(310, 'Separate · 3 Chromes', [320, 310, 310], '#3d4864', '#d3dae9', 'var(--text-2)');
    const badge = el('div', 'badge', chart, '&minus;33%');
    badge.style.left = px(960 - 262);
    badge.style.fontSize = '72px';
    badge.style.top = px(30 + 76 + 60 - 58);
    const foot = el('div', 'cfoot', chart, 'Measured · 3 workers');
    foot.style.top = px(310 + 76 + 120 + 60);
    s.update = (lt) => {
      cap.update(lt);
      const rowIn = (r, st) => {
        const p = E.outExpo(pr(lt, st, 0.5));
        r.r.style.opacity = clamp(p * 3);
        r.r.style.transform = `translateX(${(1 - p) * -90}px)`;
      };
      rowIn(r1, 0.3);
      rowIn(r2, 0.5);
      const g1 = E.outExpo(pr(lt, 0.4, 0.7));
      const g2 = E.outExpo(pr(lt, 0.65, 0.8));
      r1.bar.style.clipPath = `inset(0 ${(1 - g1) * 100}% 0 0)`;
      r2.bar.style.clipPath = `inset(0 ${(1 - g2) * 100}% 0 0)`;
      r1.num.textContent = Math.round(630 * E.outCubic(pr(lt, 0.4, 0.7))) + ' MB';
      r2.num.textContent = Math.round(940 * E.outCubic(pr(lt, 0.65, 0.8))) + ' MB';
      const bp = E.outBack(pr(lt, 1.3, 0.45));
      badge.style.transform = `scale(${bp}) rotate(${(1 - clamp(bp)) * -10}deg)`;
      badge.style.opacity = clamp(bp * 3);
      const fp = E.outCubic(pr(lt, 1.55, 0.4));
      foot.style.opacity = fp;
      foot.style.transform = `translateY(${(1 - fp) * 20}px)`;
    };
  })();

  // ============ S6: data ============
  (function () {
    const s = makeScene(5);
    const cap = Caption(s.root, CAP_TOP, [
      {
        in: 0.1,
        lines: [
          { t: 'Search.', c: 'h', at: 0.1 },
          { t: '<em>Filter.</em>', c: 'h', at: 0.95 },
          { t: 'Save datasets.', c: 'h', at: 1.8 },
        ],
      },
    ]);
    const shot = Shot(s.root, {
      src: SRC.dark,
      src2: SRC.light,
      flip: { t: 2.35, x: 1054, y: 65 },
      cams: [
        { t: 0, cx: 640, cy: 330, s: 1.0 },
        { t: 0.5, cx: 560, cy: 310, s: 1.4 },
        { t: 0.95, cx: 560, cy: 310, s: 1.4 },
        { t: 1.45, cx: 330, cy: 310, s: 1.55 },
        { t: 1.85, cx: 330, cy: 310, s: 1.55 },
        { t: 2.3, cx: 1120, cy: 290, s: 1.55 },
        { t: 9, cx: 1120, cy: 290, s: 1.55 },
      ],
      boxes: [
        { x: 287, y: 51, w: 607, h: 29, t0: 0.45, t1: 0.95 },
        { x: 196, y: 440, w: 267, h: 125, t0: 1.4, t1: 1.9 },
        { x: 488, y: 97, w: 130, h: 22, t0: 1.45, t1: 1.9 },
        { x: 903, y: 49, w: 205, h: 32, t0: 2.2, label: 'Datasets' },
      ],
    });
    const chip = el('div', 'chip', shot.ovl, 'Dark + light');
    chip.style.left = '24px';
    chip.style.bottom = '24px';
    chip.style.color = 'var(--text)';
    chip.style.background = 'rgba(16,19,24,.9)';
    s.update = (lt) => {
      cap.update(lt);
      shot.update(lt);
      const a = E.outBack(pr(lt, 2.7, 0.3));
      chip.style.opacity = clamp(a * 2);
      chip.style.transform = `scale(${lerp(0.6, 1, clamp(a))})`;
    };
  })();

  // ============ S7: export ============
  (function () {
    const s = makeScene(6);
    const cap = Caption(s.root, CAP_TOP, [
      { in: 0.1, lines: [{ t: 'Export to', c: 'h' }, { t: '<em>JSON &amp; CSV</em>', c: 'h' }, { t: 'Excel-ready CSV', c: 's' }] },
    ]);
    const shot = Shot(s.root, {
      src: SRC.dark,
      cams: [
        { t: 0, cx: 1100, cy: 300, s: 1.3 },
        { t: 0.5, cx: 1389, cy: 170, s: 2.2 },
        { t: 9, cx: 1389, cy: 170, s: 2.2 },
      ],
      boxes: [{ x: 1350, y: 50, w: 78, h: 30, t0: 0.5, t1: 0.95 }],
    });
    const cw = 340, ch = Math.round(FRAME_H * 0.47);
    const mk = (kind) => {
      const c = el('div', 'card', shot.ovl);
      c.style.width = px(cw);
      c.style.height = px(ch);
      el('div', 'tab', c);
      if (kind === 'json') el('div', 'gl', c, '{ }').style.top = px(ch * 0.22);
      else {
        const g = el('div', 'cgrid', c);
        g.style.top = px(ch * 0.2);
        for (let i = 0; i < 9; i++) el('i', '', g);
      }
      el('div', 'nm', c, kind === 'json' ? 'JSON' : 'CSV');
      return c;
    };
    const cJ = mk('json'), cC = mk('csv');
    const ty = FRAME_H * 0.64;
    const tg = [
      { c: cJ, x: 270, y: ty, rot: -5, st: 0.75 },
      { c: cC, x: 690, y: ty, rot: 5, st: 0.9 },
    ];
    s.update = (lt) => {
      cap.update(lt);
      const pl = shot.update(lt);
      const bx = 1389 * pl.s + pl.tx, by = 65 * pl.s + pl.ty;
      shot.scrim.style.opacity = 0.62 * E.outCubic(pr(lt, 0.7, 0.4));
      for (const g of tg) {
        const p = pr(lt, g.st, 0.65);
        const e = E.outBack(p);
        const x = lerp(bx, g.x, e), y = lerp(by, g.y, e);
        g.c.style.left = px(x - cw / 2);
        g.c.style.top = px(y - ch / 2);
        g.c.style.opacity = clamp(p * 6);
        g.c.style.transform = `scale(${lerp(0.2, 1, e)}) rotate(${g.rot * e}deg)`;
      }
    };
  })();

  // ============ S8: ticker ============
  (function () {
    const s = makeScene(7);
    const words = ['Open source', 'AGPL-3.0', 'Free', 'Windows'];
    const rowH = FORMAT === '4x5' ? 235 : 250;
    const top0 = mid(rowH * 4);
    const rs = words.map((w, k) => {
      const r = el('div', 'trow', s.root);
      r.style.top = px(top0 + k * rowH);
      r.style.height = px(rowH);
      const tb = el('div', 'tb', r);
      const tw = el('div', 'tw', r, w);
      const ti = el('div', 'ti', r, '0' + (k + 1));
      return { r, tb, tw, ti };
    });
    const last = el('div', 'trow', s.root);
    last.style.top = px(top0 + rowH * 4);
    last.style.height = '0px';
    s.update = (lt) => {
      rs.forEach((o, k) => {
        const st = 0.1 + k * 0.38;
        const pin = E.outExpo(pr(lt, st, 0.3));
        const pout = k < 3 ? E.inCubic(pr(lt, st + 0.38, 0.2)) : 0;
        const dir = k % 2 ? 1 : -1;
        o.tw.style.transform = `translateX(${(1 - pin) * dir * 900}px)`;
        o.tw.style.opacity = clamp(pin * 4);
        o.tb.style.transformOrigin = pout > 0 ? '100% 50%' : '0% 50%';
        o.tb.style.transform = `scaleX(${pin * (1 - pout)})`;
        const covered = pout === 0 && pin > 0.55; // dark text only while the accent bar fully backs it
        o.tw.style.color = covered ? 'var(--on-accent)' : 'var(--text)';
        o.ti.style.color = covered ? 'var(--on-accent)' : 'var(--text-3)';
        o.ti.style.opacity = clamp(pin * 3);
      });
    };
  })();

  // ============ S9: CTA ============
  (function () {
    const s = makeScene(8);
    const IC = 190;
    const total = IC + 44 + 145 + 50 + 190 + 44 + 80 + 56 + 50;
    let y = mid(total);
    const iconTop = y;
    const cx = 60 + IC / 2, cy = iconTop + IC / 2;
    const glow = el('div', 'glow', s.root);
    glow.style.cssText = `position:absolute;border-radius:50%;width:${IC * 4}px;height:${IC * 4}px;left:${cx - IC * 2}px;top:${cy - IC * 2}px;background:radial-gradient(circle, rgba(79,191,179,.3), transparent 62%)`;
    const icon = el('div', 'icon', s.root);
    icon.style.cssText += `left:60px;top:${iconTop}px;width:${IC}px;height:${IC}px;filter:drop-shadow(0 24px 40px rgba(0,0,0,.55))`;
    img(SRC.icon, '', icon);
    const dl = el('div', 'dl', s.root);
    dl.style.left = px(W - 60 - 112);
    dl.style.top = px(cy - 56);
    dl.innerHTML = '<svg width="64" height="64" viewBox="0 0 24 24" fill="none" stroke="#08201d" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round"><path d="M12 4v13M6 12l6 6 6-6"/></svg>';
    y += IC + 44;
    const cap = Caption(s.root, y, [{ in: 0.3, lines: [{ t: 'Free <em>download</em>', c: 'wm' }] }]);
    y += 145 + 50;
    const card = el('div', 'url', s.root);
    card.style.top = px(y);
    const u1 = el('div', 'u1', card, 'github.com/abhishekairan/');
    const u2 = el('div', 'u2', card, 'Branch-Scrapper');
    y += 190 + 44;
    const chipsTop = y;
    const chipDefs = [['v0.1.0', ''], ['Windows', ''], ['AGPL-3.0', '']];
    const chips = chipDefs.map(([t]) => el('div', 'chip dim', s.root, t));
    // lay chips out in a row from the left
    let cxp = 60;
    chips.forEach((c) => {
      c.style.left = px(cxp);
      c.style.top = px(chipsTop);
      c.dataset.w = '0';
    });
    y += 80 + 56;
    const sup = el('div', 'cfoot', s.root, 'Support: Buy Me a Coffee');
    sup.style.left = '60px';
    sup.style.top = px(y);
    sup.style.color = 'var(--text-2)';
    sup.style.font = '500 44px/1.1 var(--display)';
    let laid = false;
    s.update = (lt) => {
      if (READY && !laid) {
        let x = 60;
        chips.forEach((c) => { c.style.left = px(x); x += c.offsetWidth + 20; });
        laid = true;
      }
      const ip = pr(lt, 0.05, 0.6);
      const float = Math.sin(lt * 2.2) * 6 * E.outCubic(pr(lt, 0.8, 0.6));
      icon.style.transform = `translateY(${float}px) scale(${lerp(0.3, 1, E.outBack(ip))}) rotate(${(1 - E.outExpo(ip)) * -12}deg)`;
      icon.style.opacity = clamp(ip * 5);
      glow.style.opacity = E.outCubic(pr(lt, 0.1, 0.6)) * (0.8 + 0.2 * Math.sin(lt * 2.6));
      const dp = E.outBack(pr(lt, 0.45, 0.4));
      const bounce = Math.abs(Math.sin(lt * 4.2)) * 10 * E.outCubic(pr(lt, 0.9, 0.3));
      dl.style.transform = `scale(${dp}) translateY(${bounce}px)`;
      dl.style.opacity = clamp(dp * 3);
      cap.update(lt);
      const cp = E.outExpo(pr(lt, 0.4, 0.55));
      card.style.opacity = clamp(cp * 3);
      card.style.transform = `translateY(${(1 - cp) * 90}px)`;
      const t1 = E.outCubic(pr(lt, 0.55, 0.35)), t2 = E.outCubic(pr(lt, 0.8, 0.3));
      u1.style.clipPath = `inset(0 ${(1 - t1) * 100}% 0 0)`;
      u2.style.clipPath = `inset(0 ${(1 - t2) * 100}% 0 0)`;
      chips.forEach((c, i) => {
        const p = E.outBack(pr(lt, 0.75 + i * 0.08, 0.35));
        c.style.opacity = clamp(p * 3);
        c.style.transform = `translateY(${(1 - clamp(p)) * 40}px) scale(${lerp(0.7, 1, clamp(p))})`;
      });
      const sp = E.outCubic(pr(lt, 1.0, 0.4));
      sup.style.opacity = sp;
      sup.style.transform = `translateY(${(1 - sp) * 20}px)`;
    };
  })();

  // ---------- renderAt ----------
  function resetStyle(s) {
    const st = s.root.style;
    st.transform = 'none';
    st.clipPath = 'none';
    st.filter = 'none';
    st.opacity = '1';
  }

  window.renderAt = function (t) {
    t = clamp(t, 0, TOTAL - 1e-6);
    edge.style.display = 'none';
    // which scene is current
    let cur = 0;
    for (let i = 0; i < T.length - 1; i++) if (t >= T[i]) cur = i;
    scenes.forEach((s) => {
      const i = s.i;
      const vis = i === cur || (i === cur - 1 && t < T[cur] + D);
      s.root.style.display = vis ? 'block' : 'none';
      if (!vis) return;
      resetStyle(s);
      s.bg.update(t);
      s.update(t - T[i]);
    });
    // transition between scenes[cur-1] and scenes[cur]
    if (cur > 0 && t < T[cur] + D) {
      const tr = TRANS[cur];
      const p = pr(t, T[cur], D);
      const e = E.inOutCubic(p);
      const nw = scenes[cur].root.style, ol = scenes[cur - 1].root.style;
      const bar = 26;
      const setEdge = (l, tp, w, h) => {
        edge.style.display = 'block';
        edge.style.left = px(l);
        edge.style.top = px(tp);
        edge.style.width = px(w);
        edge.style.height = px(h);
      };
      switch (tr.type) {
        case 'curtainUp': {
          const y = H * (1 - e);
          nw.clipPath = `inset(${y}px 0 0 0)`;
          setEdge(0, y - bar, W, bar);
          ol.transform = `scale(${1 - 0.05 * e})`;
          ol.filter = `brightness(${1 - 0.5 * e})`;
          break;
        }
        case 'curtainLeft': {
          const x = W * (1 - e);
          nw.clipPath = `inset(0 0 0 ${x}px)`;
          setEdge(x - bar, 0, bar, H);
          ol.transform = `translateX(${-60 * e}px)`;
          ol.filter = `brightness(${1 - 0.5 * e})`;
          break;
        }
        case 'curtainRight': {
          const x = W * e;
          nw.clipPath = `inset(0 ${W - x}px 0 0)`;
          setEdge(x, 0, bar, H);
          ol.transform = `translateX(${60 * e}px)`;
          ol.filter = `brightness(${1 - 0.5 * e})`;
          break;
        }
        case 'pushLeft': {
          nw.transform = `translateX(${W * (1 - e)}px)`;
          ol.transform = `translateX(${-W * 0.3 * e}px)`;
          ol.filter = `brightness(${1 - 0.5 * e})`;
          break;
        }
        case 'pushUp': {
          nw.transform = `translateY(${H * (1 - e)}px)`;
          ol.transform = `translateY(${-H * 0.25 * e}px)`;
          ol.filter = `brightness(${1 - 0.5 * e})`;
          break;
        }
        case 'iris': {
          const R = E.inOutCubic(p) * Math.hypot(W, H);
          nw.clipPath = `circle(${R}px at ${tr.x}px ${tr.y}px)`;
          ol.filter = `brightness(${1 - 0.4 * e})`;
          break;
        }
        case 'zoom': {
          const o = E.outCubic(p);
          nw.opacity = o;
          nw.transform = `scale(${0.88 + 0.12 * o})`;
          ol.transform = `scale(${1 + 0.14 * o})`;
          ol.filter = `brightness(${1 - 0.4 * o})`;
          break;
        }
      }
    }
  };

  window.TIMELINE = { fps: FPS, total: TOTAL, scenes: T, w: W, h: H, format: FORMAT };

  // ---------- readiness ----------
  window.ready = (async () => {
    const fonts = [
      '800 100px "Bricolage Grotesque"', '600 48px "Bricolage Grotesque"', '500 56px "Bricolage Grotesque"',
      '700 44px "JetBrains Mono"', '800 100px "JetBrains Mono"', '500 44px "JetBrains Mono"',
    ];
    try { await Promise.all(fonts.map((f) => document.fonts.load(f))); } catch (e) { /* offline: fallbacks */ }
    await document.fonts.ready;
    await Promise.all(imgs.map((i) => (i.decode ? i.decode().catch(() => {}) : Promise.resolve())));
    READY = true;
    // prime label measurements across the timeline
    for (const tt of [0.5, 2.9, 5.5, 8.0, 11, 13.0, 15.5, 18, 20]) window.renderAt(tt);
    window.renderAt(0);
    return true;
  })();
})();
