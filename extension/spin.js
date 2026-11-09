// Renders the avatar's 360° spinning GIF right in the browser (no Python, no GPU):
// a small CPU rasteriser ported from render_gif.py, a median-cut palette, and a GIF writer.
// Works in the extension popup and in Node (the test passes its own PNG decode/encode).

const dot = (a, b) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
const cross = (a, b) => [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]];
const norm = a => { const l = Math.hypot(a[0], a[1], a[2]) || 1; return [a[0] / l, a[1] / l, a[2] / l]; };

// ------------------------------------------------------------------ loading
function parseObj(text) {
  const V = [], VT = [], VN = [], faces = [], mats = [];
  let cur = '';
  for (const line of text.split('\n')) {
    const c0 = line.charCodeAt(0), c1 = line.charCodeAt(1);
    if (c0 === 118 /* v */) {
      const p = line.trim().split(/\s+/);
      if (c1 === 32) V.push(+p[1], +p[2], +p[3]);
      else if (c1 === 116) VT.push(+p[1], +p[2]);
      else if (c1 === 110) VN.push(+p[1], +p[2], +p[3]);
    } else if (c0 === 102 && c1 === 32 /* f */) {
      const idx = line.trim().split(/\s+/).slice(1).map(t => {
        const q = t.split('/');
        return [parseInt(q[0], 10) - 1, q[1] ? parseInt(q[1], 10) - 1 : -1, q[2] ? parseInt(q[2], 10) - 1 : -1];
      });
      for (let k = 1; k < idx.length - 1; k++) { faces.push(idx[0], idx[k], idx[k + 1]); mats.push(cur); }
    } else if (line.startsWith('usemtl ')) cur = line.slice(7).trim();
  }
  return { V, VT: VT.length ? VT : [0, 0], VN: VN.length ? VN : [0, 1, 0], faces, mats };
}

function parseMtl(text) {
  const mats = {};
  let cur = null;
  for (const line of text.split('\n')) {
    const p = line.trim().split(/\s+/);
    if (p[0] === 'newmtl') { cur = p[1]; mats[cur] = {}; }
    else if (cur && p.length > 1) mats[cur][p[0]] = p.slice(1);
  }
  return mats;
}

class Scene {
  static async load(av, decodePng) {
    const s = new Scene();
    const dec = new TextDecoder();
    const obj = parseObj(dec.decode(av.obj));
    const mtl = parseMtl(dec.decode(av.mtl));
    const files = Object.fromEntries(av.textures.map(t => [t.name, t.data]));
    const texCache = {};
    const tex = async name => {
      if (!(name in texCache)) texCache[name] = files[name] ? await decodePng(files[name]) : null;
      return texCache[name];
    };
    const names = [...new Set(obj.mats)].sort();
    s.color = []; s.glow = [];
    for (const n of names) {
      const m = mtl[n] || {};
      let t = m.map_Kd ? await tex(m.map_Kd[0]) : null;
      if (!t) {            // plain colour material
        const kd = (m.Kd || ['0.8', '0.8', '0.8']).map(Number);
        t = { w: 2, h: 2, data: new Uint8Array(16) };
        for (let i = 0; i < 4; i++) t.data.set([kd[0] * 255, kd[1] * 255, kd[2] * 255, 255], i * 4);
      }
      s.color.push(t);
      s.glow.push(m.map_Ke ? await tex(m.map_Ke[0]) : null);
    }
    const T = obj.faces.length / 3;
    s.T = T;
    s.P = new Float64Array(T * 9); s.N = new Float32Array(T * 9); s.UV = new Float32Array(T * 6); s.M = new Int32Array(T);
    const { V, VT, VN } = obj;
    for (let t = 0; t < T; t++) {
      const mi = names.indexOf(obj.mats[t]);
      s.M[t] = mi;
      const { w, h } = s.color[mi];
      for (let k = 0; k < 3; k++) {
        const [vi, ti, ni] = obj.faces[t * 3 + k];
        s.P.set([V[vi * 3], V[vi * 3 + 1], V[vi * 3 + 2]], t * 9 + k * 3);
        const n = ni >= 0 ? ni : 0;
        s.N.set([VN[n * 3], VN[n * 3 + 1], VN[n * 3 + 2]], t * 9 + k * 3);
        const u = ti >= 0 ? ti : 0;
        s.UV[t * 6 + k * 2] = VT[u * 2] * w - 0.5;
        s.UV[t * 6 + k * 2 + 1] = (1 - VT[u * 2 + 1]) * h - 0.5;
      }
    }
    return s;
  }

  // one frame, size x size RGBA (straight alpha), rendered at size*ss and box-filtered down
  render(eye, target, fov, size, ss) {
    const S = size * ss;
    const f = norm([target[0] - eye[0], target[1] - eye[1], target[2] - eye[2]]);
    const r = norm(cross(f, [0, 1, 0]));
    const u = cross(r, f);
    const foc = (S / 2) / Math.tan(fov * Math.PI / 360);
    const depth = new Float32Array(S * S).fill(Infinity);
    const tid = new Int32Array(S * S).fill(-1);
    const B0 = new Float32Array(S * S), B1 = new Float32Array(S * S);
    const { P, UV, M } = this;
    const sx = [0, 0, 0], sy = [0, 0, 0], z = [0, 0, 0];
    for (let t = 0; t < this.T; t++) {
      let behind = false;
      for (let k = 0; k < 3; k++) {
        const o = t * 9 + k * 3;
        const p = [P[o] - eye[0], P[o + 1] - eye[1], P[o + 2] - eye[2]];
        const zc = dot(p, f);
        if (zc <= 0.05) { behind = true; break; }
        z[k] = zc;
        sx[k] = S / 2 + foc * dot(p, r) / zc;
        sy[k] = S / 2 - foc * dot(p, u) / zc;
      }
      if (behind) continue;
      const [x0, x1, x2] = sx, [y0, y1, y2] = sy;
      const minx = Math.max(Math.floor(Math.min(x0, x1, x2)), 0), maxx = Math.min(Math.floor(Math.max(x0, x1, x2)) + 1, S - 1);
      const miny = Math.max(Math.floor(Math.min(y0, y1, y2)), 0), maxy = Math.min(Math.floor(Math.max(y0, y1, y2)) + 1, S - 1);
      if (minx > maxx || miny > maxy) continue;
      const den = (y1 - y2) * (x0 - x2) + (x2 - x1) * (y0 - y2);
      if (Math.abs(den) < 1e-9) continue;
      const tex = this.color[M[t]], tw = tex.w, th = tex.h, td = tex.data;
      const u0 = UV[t * 6], v0 = UV[t * 6 + 1], u1 = UV[t * 6 + 2], v1 = UV[t * 6 + 3], u2 = UV[t * 6 + 4], v2 = UV[t * 6 + 5];
      const iz0 = 1 / z[0], iz1 = 1 / z[1], iz2 = 1 / z[2];
      for (let y = miny; y <= maxy; y++) {
        const gy = y + 0.5;
        for (let x = minx; x <= maxx; x++) {
          const gx = x + 0.5;
          const w0 = ((y1 - y2) * (gx - x2) + (x2 - x1) * (gy - y2)) / den;
          if (w0 < 0) continue;
          const w1 = ((y2 - y0) * (gx - x2) + (x0 - x2) * (gy - y2)) / den;
          if (w1 < 0) continue;
          const w2 = 1 - w0 - w1;
          if (w2 < 0) continue;
          const zz = 1 / Math.max(w0 * iz0 + w1 * iz1 + w2 * iz2, 1e-9);
          const i = y * S + x;
          if (zz >= depth[i]) continue;
          const p0 = w0 * iz0 * zz, p1 = w1 * iz1 * zz, p2 = 1 - p0 - p1;
          // alpha test: see-through parts of hair / accessories
          const tu = Math.round(p0 * u0 + p1 * u1 + p2 * u2), tv = Math.round(p0 * v0 + p1 * v1 + p2 * v2);
          const tx = ((tu % tw) + tw) % tw, ty = tv < 0 ? 0 : tv >= th ? th - 1 : tv;
          if (td[(ty * tw + tx) * 4 + 3] <= 100) continue;
          depth[i] = zz; tid[i] = t; B0[i] = p0; B1[i] = p1;
        }
      }
    }
    // shade
    const key = norm([-f[0] + 0.55 * u[0] - 0.35 * r[0], -f[1] + 0.55 * u[1] - 0.35 * r[1], -f[2] + 0.55 * u[2] - 0.35 * r[2]]);
    const fill = norm([-f[0] - 0.3 * u[0] + 0.4 * r[0], -f[1] - 0.3 * u[1] + 0.4 * r[1], -f[2] - 0.3 * u[2] + 0.4 * r[2]]);
    const big = new Uint8ClampedArray(S * S * 4);
    const N = this.N, c = [0, 0, 0, 0], g = [0, 0, 0, 0];
    for (let i = 0; i < S * S; i++) {
      const t = tid[i];
      if (t < 0) continue;
      const p0 = B0[i], p1 = B1[i], p2 = 1 - p0 - p1;
      const uu = UV[t * 6] * p0 + UV[t * 6 + 2] * p1 + UV[t * 6 + 4] * p2;
      const vv = UV[t * 6 + 1] * p0 + UV[t * 6 + 3] * p1 + UV[t * 6 + 5] * p2;
      const o = t * 9;
      const n = norm([N[o] * p0 + N[o + 3] * p1 + N[o + 6] * p2, N[o + 1] * p0 + N[o + 4] * p1 + N[o + 7] * p2, N[o + 2] * p0 + N[o + 5] * p1 + N[o + 8] * p2]);
      const tex = this.color[M[t]];
      bilinear(tex, uu, vv, c);
      let e0 = 0, e1 = 0, e2 = 0;
      const gl = this.glow[M[t]];
      if (gl) { bilinear(gl, uu * gl.w / tex.w, vv * gl.h / tex.h, g); e0 = g[0]; e1 = g[1]; e2 = g[2]; }
      const lam = 0.62 + 0.34 * Math.min(Math.max(dot(n, key), 0), 1) + 0.08 * Math.min(Math.max(dot(n, fill), 0), 1);
      big[i * 4] = c[0] * lam + e0 * 0.9; big[i * 4 + 1] = c[1] * lam + e1 * 0.9; big[i * 4 + 2] = c[2] * lam + e2 * 0.9; big[i * 4 + 3] = 255;
    }
    // box filter down (premultiplied, so edges don't go dark)
    const out = new Uint8ClampedArray(size * size * 4);
    for (let y = 0; y < size; y++) for (let x = 0; x < size; x++) {
      let R = 0, G = 0, B = 0, A = 0;
      for (let j = 0; j < ss; j++) for (let k = 0; k < ss; k++) {
        const q = ((y * ss + j) * S + x * ss + k) * 4, a = big[q + 3];
        R += big[q] * a; G += big[q + 1] * a; B += big[q + 2] * a; A += a;
      }
      const o = (y * size + x) * 4;
      if (A) { out[o] = R / A; out[o + 1] = G / A; out[o + 2] = B / A; out[o + 3] = A / (ss * ss); }
    }
    return out;
  }
}

function bilinear(t, u, v, out) {
  const { w, h, data } = t;
  u = ((u % w) + w) % w; v = v < 0 ? 0 : v > h - 1 ? h - 1 : v;
  const x0 = Math.floor(u), y0 = Math.floor(v), fx = u - x0, fy = v - y0;
  const x1 = (x0 + 1) % w, y1 = Math.min(y0 + 1, h - 1);
  const a = (y0 * w + x0) * 4, b = (y0 * w + x1) * 4, c = (y1 * w + x0) * 4, d = (y1 * w + x1) * 4;
  for (let k = 0; k < 4; k++) {
    out[k] = data[a + k] * (1 - fx) * (1 - fy) + data[b + k] * fx * (1 - fy) + data[c + k] * (1 - fx) * fy + data[d + k] * fx * fy;
  }
}

// ------------------------------------------------------------------ palette (median cut)
function palette(frames, n = 255) {
  const cnt = new Float64Array(1 << 18), sr = new Float64Array(1 << 18), sg = new Float64Array(1 << 18), sb = new Float64Array(1 << 18);
  for (const f of frames) for (let i = 0; i < f.length; i += 4) {
    if (f[i + 3] < 128) continue;
    const k = ((f[i] >> 2) << 12) | ((f[i + 1] >> 2) << 6) | (f[i + 2] >> 2);
    cnt[k]++; sr[k] += f[i]; sg[k] += f[i + 1]; sb[k] += f[i + 2];
  }
  const bins = [];
  for (let k = 0; k < cnt.length; k++) if (cnt[k]) bins.push(k);
  const ch = (k, c) => (k >> (12 - 6 * c)) & 63;
  const boxOf = list => {
    let total = 0; const lo = [63, 63, 63], hi = [0, 0, 0];
    for (const k of list) { total += cnt[k]; for (let c = 0; c < 3; c++) { const v = ch(k, c); if (v < lo[c]) lo[c] = v; if (v > hi[c]) hi[c] = v; } }
    return { list, total, range: Math.max(hi[0] - lo[0], hi[1] - lo[1], hi[2] - lo[2]), lo, hi };
  };
  const boxes = bins.length ? [boxOf(bins)] : [];
  while (boxes.length < n) {
    let best = -1, score = 0;
    boxes.forEach((b, i) => { const s = b.list.length > 1 ? b.total * b.range : 0; if (s > score) { score = s; best = i; } });
    if (best < 0) break;
    const b = boxes[best];
    const sizes = [0, 1, 2].map(c => b.hi[c] - b.lo[c]);
    const c = sizes.indexOf(Math.max(...sizes));
    b.list.sort((p, q) => ch(p, c) - ch(q, c));
    let acc = 0, cut = 1;
    for (let i = 0; i < b.list.length - 1; i++) { acc += cnt[b.list[i]]; if (acc >= b.total / 2) { cut = i + 1; break; } cut = i + 1; }
    boxes.splice(best, 1, boxOf(b.list.slice(0, cut)), boxOf(b.list.slice(cut)));
  }
  const pal = new Uint8Array(768);
  boxes.forEach((b, i) => {
    let R = 0, G = 0, B = 0, C = 0;
    for (const k of b.list) { R += sr[k]; G += sg[k]; B += sb[k]; C += cnt[k]; }
    pal.set([Math.round(R / C), Math.round(G / C), Math.round(B / C)], i * 3);
  });
  return { pal, used: boxes.length };
}

function indexFrames(frames, pal, used) {
  const cache = new Int16Array(1 << 18).fill(-1);
  return frames.map(f => {
    const px = new Uint8Array(f.length / 4);
    for (let i = 0, j = 0; i < f.length; i += 4, j++) {
      if (f[i + 3] < 128) { px[j] = 255; continue; }
      const k = ((f[i] >> 2) << 12) | ((f[i + 1] >> 2) << 6) | (f[i + 2] >> 2);
      let best = cache[k];
      if (best < 0) {
        let bd = Infinity;
        for (let p = 0; p < used; p++) {
          const dr = pal[p * 3] - f[i], dg = pal[p * 3 + 1] - f[i + 1], db = pal[p * 3 + 2] - f[i + 2];
          const d = dr * dr + dg * dg + db * db;
          if (d < bd) { bd = d; best = p; }
        }
        cache[k] = best;
      }
      px[j] = best;
    }
    return px;
  });
}

// ------------------------------------------------------------------ GIF writer (full frames)
function lzw(data) {
  const out = [];
  let cur = 0, nbits = 0, size = 9, nxt = 258;
  const table = new Map();
  const emit = code => {
    cur |= code << nbits; nbits += size;
    while (nbits >= 8) { out.push(cur & 255); cur >>>= 8; nbits -= 8; }
  };
  emit(256);
  let prefix = data[0];
  for (let i = 1; i < data.length; i++) {
    const b = data[i], key = (prefix << 8) | b, c = table.get(key);
    if (c !== undefined) { prefix = c; continue; }
    emit(prefix);
    if (nxt === 4096) { emit(256); table.clear(); nxt = 258; size = 9; }
    else { if (nxt >= (1 << size)) size++; table.set(key, nxt++); }
    prefix = b;
  }
  emit(prefix); emit(257);
  if (nbits) out.push(cur & 255);
  return out;
}

export function writeGif(frames, w, h, pal, ms) {
  const bytes = [];
  const push = (...a) => { for (const v of a) bytes.push(v); };
  const u16 = v => [v & 255, v >> 8];
  push(...[...'GIF89a'].map(ch => ch.charCodeAt(0)), ...u16(w), ...u16(h), 0xF7, 255, 0);
  for (let i = 0; i < 765; i++) bytes.push(pal[i]);
  push(0, 0, 0);
  push(0x21, 0xFF, 0x0B, ...[...'NETSCAPE2.0'].map(ch => ch.charCodeAt(0)), 3, 1, 0, 0, 0);
  const delay = Math.max(2, Math.round(ms / 10));
  for (const px of frames) {
    push(0x21, 0xF9, 4, (2 << 2) | 1, ...u16(delay), 255, 0);     // dispose to background, index 255 transparent
    push(0x2C, 0, 0, 0, 0, ...u16(w), ...u16(h), 0, 8);
    const data = lzw(px);
    for (let i = 0; i < data.length; i += 255) {
      const n = Math.min(255, data.length - i);
      bytes.push(n);
      for (let k = 0; k < n; k++) bytes.push(data[i + k]);
    }
    bytes.push(0);
  }
  bytes.push(0x3B);
  return Uint8Array.from(bytes);
}

// ------------------------------------------------------------------ the whole thing
// av = fetchAvatar() result; returns { gif, front } (Uint8Array GIF, Uint8Array PNG)
export async function makeSpin(av, { decodePng, encodePng, size = 400, frames = 36, ss = 2, ms = 70, onFrame } = {}) {
  const scene = await Scene.load(av, decodePng);
  const cam = JSON.parse(new TextDecoder().decode(av.camera));
  const lo = ['x', 'y', 'z'].map(k => cam.aabb.min[k]), hi = ['x', 'y', 'z'].map(k => cam.aabb.max[k]);
  const center = lo.map((v, i) => (v + hi[i]) / 2);
  const fov = cam.camera.fov;
  const d = ['x', 'y', 'z'].map(k => cam.camera.direction[k]);
  const elev = Math.asin(Math.max(-0.6, Math.min(0.6, d[1] / Math.hypot(...d))));     // Roblox's own camera tilt
  const radius = Math.hypot(hi[0] - lo[0], hi[1] - lo[1], hi[2] - lo[2]) / 2;
  const dist = radius / Math.sin(fov * Math.PI / 360) * 1.02;                           // whole avatar always in frame

  const shots = [];
  for (let i = 0; i < frames; i++) {
    const yaw = 2 * Math.PI * i / frames;
    const off = [Math.sin(yaw) * Math.cos(elev), Math.sin(elev), -Math.cos(yaw) * Math.cos(elev)];
    shots.push(scene.render(center.map((c, k) => c + off[k] * dist), center, fov, size, ss));
    if (onFrame) await onFrame(i + 1, frames);
  }
  // crop every frame to the same box around the avatar
  let x0 = size, y0 = size, x1 = -1, y1 = -1;
  for (const s of shots) for (let y = 0; y < size; y++) for (let x = 0; x < size; x++) {
    if (s[(y * size + x) * 4 + 3]) { if (x < x0) x0 = x; if (x > x1) x1 = x; if (y < y0) y0 = y; if (y > y1) y1 = y; }
  }
  const pad = Math.max(4, Math.floor(size / 60));
  x0 = Math.max(x0 - pad, 0); y0 = Math.max(y0 - pad, 0); x1 = Math.min(x1 + pad, size - 1); y1 = Math.min(y1 + pad, size - 1);
  const W = x1 - x0 + 1, H = y1 - y0 + 1;
  const crops = shots.map(s => {
    const c = new Uint8ClampedArray(W * H * 4);
    for (let y = 0; y < H; y++) c.set(s.subarray(((y + y0) * size + x0) * 4, ((y + y0) * size + x1 + 1) * 4), y * W * 4);
    return c;
  });
  const { pal, used } = palette(crops);
  const gif = writeGif(indexFrames(crops, pal, used), W, H, pal, ms);
  const front = encodePng ? await encodePng(crops[0], W, H) : null;
  return { gif, front, width: W, height: H };
}
