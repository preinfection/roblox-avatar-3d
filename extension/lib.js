// Core logic, shared by the popup and the Node test.
import { makeZip } from './zip.js';

// Roblox serves 3D files from t0..t7.rbxcdn.com; the host is picked from the hash.
export function cdnUrl(hash) {
  let i = 31;
  for (const ch of hash.slice(0, 38)) i ^= ch.charCodeAt(0);
  return `https://t${i % 8}.rbxcdn.com/${hash}`;
}

async function fetchHash(hash, fetchFn) {
  const urls = [cdnUrl(hash), ...[0, 1, 2, 3, 4, 5, 6, 7].map(n => `https://t${n}.rbxcdn.com/${hash}`)];
  for (const u of [...new Set(urls)]) {
    try {
      const r = await fetchFn(u);
      if (r.ok) return new Uint8Array(await r.arrayBuffer());
    } catch { /* try the next host */ }
  }
  throw new Error('File not found on Roblox CDN');
}

export async function userByName(name, fetchFn = fetch) {
  const r = await fetchFn('https://users.roblox.com/v1/usernames/users', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ usernames: [name], excludeBannedUsers: false }),
  });
  if (!r.ok) throw new Error('Lookup failed');
  const u = (await r.json()).data?.[0];
  if (!u) throw new Error('User not found');
  return { id: u.id, name: u.name, displayName: u.displayName };
}

export async function userById(id, fetchFn = fetch) {
  const r = await fetchFn(`https://users.roblox.com/v1/users/${id}`);
  if (!r.ok) throw new Error('User not found');
  const u = await r.json();
  return { id: u.id, name: u.name, displayName: u.displayName };
}

// Is a gear (held tool) equipped? From the avatar's public item list. null if Roblox didn't answer.
export async function avatarHasGear(id, fetchFn = fetch) {
  try {
    const r = await fetchFn(`https://avatar.roblox.com/v1/users/${id}/avatar`);
    if (!r.ok) return null;
    return ((await r.json()).assets || []).some(a => a.assetType?.name === 'Gear');
  } catch { return null; }
}

export async function headshotUrl(id, fetchFn = fetch) {
  const r = await fetchFn(`https://thumbnails.roblox.com/v1/users/avatar-headshot?userIds=${id}&size=150x150&format=Png&isCircular=false`);
  if (!r.ok) return null;
  return (await r.json()).data?.[0]?.imageUrl || null;
}

// Name every texture by what it is, using the material file:
//   Player* materials are the body (skin + shirt/pants/face baked together),
//   Handle* materials are accessories; map_Bump/map_Ns/map_Ke are normal/roughness/glow maps.
export function textureNames(mtlText, hashes) {
  const KIND = { map_Kd: '', map_Ka: '', map_d: '', map_Bump: '_normal', map_bump: '_normal', map_Ns: '_roughness', map_Ke: '_glow' };
  const names = {};
  const taken = new Set();
  const acc = {};            // accessory material -> number
  let bodies = 0;
  let mat = '';
  for (const line of mtlText.split(/\r?\n/)) {
    const p = line.trim().split(/\s+/);
    if (p[0] === 'newmtl') { mat = p[1] || ''; continue; }
    if (!(p[0] in KIND) || !p[1] || names[p[1]]) continue;
    let base;
    if (/^Player/i.test(mat)) base = ++bodies === 1 ? 'body' : `body_${bodies}`;
    else {
      if (!acc[mat]) acc[mat] = Object.keys(acc).length + 1;
      base = `accessory_${acc[mat]}`;
    }
    let n = base + KIND[p[0]], k = 2;
    while (taken.has(n)) n = `${base}${KIND[p[0]]}_${k++}`;
    taken.add(n);
    names[p[1]] = n + '.png';
  }
  hashes.forEach((h, i) => { if (!names[h]) names[h] = `extra_${i + 1}.png`; });
  return names;
}

export function readme(user) {
  return `${user} - Roblox avatar in 3D
==========================================

WHAT'S IN THIS FOLDER
  avatar_spin.gif   The avatar spinning 360 degrees, transparent background.
  avatar_front.png  The avatar from the front, transparent background.
  avatar.obj        The 3D model (body, clothes, hair, accessories). Open this one.
  avatar.mtl        Materials: tells the model which picture goes where. Keep it next to avatar.obj.
  textures/         The pictures painted on the model:
                      body.png             skin with the shirt, pants and face on it
                      accessory_N.png      the colours of each accessory (hair, hats...)
                      *_normal.png         surface bumps         (optional detail)
                      *_roughness.png      shininess             (optional detail)
                      *_glow.png           parts that glow       (optional detail)
  camera.json       The angle Roblox uses for its own thumbnail.
  render_gif.py     Makes the spinning GIF again at another size (see below).
  README.txt        This file.

OPEN IT
  Windows:  right-click avatar.obj > Open with > 3D Viewer.
  Blender:  File > Import > Wavefront (.obj) > avatar.obj  (textures load automatically).
  Keep the files together - moving avatar.obj on its own loses the colours.

A BIGGER OR SMOOTHER GIF  (optional, needs Python)
  pip install numpy pillow
  python render_gif.py --size 720 --frames 48
`;
}

// Fetch the avatar's files once: { obj, mtl, camera, textures: [{ name, data }] }  (all Uint8Array)
// meta = the JSON at avatar-3d's imageUrl: { obj, mtl, textures[], camera, aabb }
// hasGear: from avatarHasGear() (true / false), or null if unknown
export async function fetchAvatar(meta, fetchFn = fetch, onStep = () => {}, hasGear = null) {
  const total = 2 + meta.textures.length;
  let done = 0;
  const step = () => onStep(++done, total);
  const dec = new TextDecoder(), enc = new TextEncoder();
  const objRaw = await fetchHash(meta.obj, fetchFn); step();
  const mtlRaw = dec.decode(await fetchHash(meta.mtl, fetchFn)); step();
  const texNames = textureNames(mtlRaw, meta.textures);
  const textures = [];
  for (const h of meta.textures) {
    textures.push({ name: `textures/${texNames[h]}`, data: await fetchHash(h, fetchFn) });
    step();
  }
  // gear in a hand: raise that arm and stand the gear up (see standGear)
  const { text: objText, bounds } = standGear(dec.decode(objRaw), meta.camera?.position, hasGear);
  const aabb = bounds ? { min: { x: bounds[0][0], y: bounds[0][1], z: bounds[0][2] }, max: { x: bounds[1][0], y: bounds[1][1], z: bounds[1][2] } } : meta.aabb;
  // OBJ: point it at the material file so Blender & co. load textures automatically
  const obj = enc.encode('mtllib avatar.mtl\n' + objText);
  // MTL: texture hashes -> textures/<name>.png
  let mtl = mtlRaw;
  for (const [h, n] of Object.entries(texNames)) mtl = mtl.split(h).join(`textures/${n}`);
  const camera = enc.encode(JSON.stringify({ camera: meta.camera, aabb }, null, 2));
  return { obj, mtl: enc.encode(mtl), camera, textures };
}

// Roblox exports held gear in its grip for a raised arm but leaves the arm hanging down, so a
// staff or sword lies flat. Raise that arm forward and turn the gear with it,
// around the shoulder: Roblox's own holding pose. Same logic as stand_gear() in cli/avatar3d.py.
// Returns { text, bounds } (bounds = [min, max] of the new model, or null if nothing changed).
export function standGear(text, camera = null, hasGear = null) {
  if (hasGear === false) return { text, bounds: null };
  const lines = text.split('\n');
  const V = [], N = [], vline = [], nline = [];
  const groups = new Map();
  let cur = '';
  lines.forEach((line, k) => {
    if (line.startsWith('v ')) { V.push(line.trim().split(/\s+/).slice(1, 4).map(Number)); vline.push(k); }
    else if (line.startsWith('vn ')) { N.push(line.trim().split(/\s+/).slice(1, 4).map(Number)); nline.push(k); }
    else if (line.startsWith('g ') || line.startsWith('o ')) cur = line.slice(2).trim();
    else if (line.startsWith('f ')) {
      if (!groups.has(cur)) groups.set(cur, [new Set(), new Set()]);
      const g = groups.get(cur);
      for (const t of line.trim().split(/\s+/).slice(1)) {
        const q = t.split('/');
        const vi = parseInt(q[0], 10), ni = parseInt(q[2], 10);
        if (vi > 0) g[0].add(vi - 1);
        if (ni > 0) g[1].add(ni - 1);
      }
    }
  });
  if (!V.length || !groups.size) return { text, bounds: null };
  const box = ix => {
    const lo = [Infinity, Infinity, Infinity], hi = [-Infinity, -Infinity, -Infinity];
    for (const i of ix) for (let a = 0; a < 3; a++) { lo[a] = Math.min(lo[a], V[i][a]); hi[a] = Math.max(hi[a], V[i][a]); }
    return [lo, hi];
  };
  const body = new Map();
  for (const [g, [v]] of groups) if (g.toLowerCase().startsWith('player') && v.size) body.set(g, box(v));
  if (!body.size) return { text, bounds: null };
  const blo = [0, 1, 2].map(a => Math.min(...[...body.values()].map(b => b[0][a])));
  const bhi = [0, 1, 2].map(a => Math.max(...[...body.values()].map(b => b[1][a])));
  const lat = bhi[0] - blo[0] >= bhi[2] - blo[2] ? 0 : 2, dep = 2 - lat;   // left-right, front-back
  // the avatar's front: the side Roblox's thumbnail camera is on (Roblox avatars face -z)
  const cam = camera ? [camera.x, camera.y, camera.z] : null;
  const front = cam ? (cam[dep] > (blo[dep] + bhi[dep]) / 2 ? 1 : -1) : -1;
  const mid = (blo[lat] + bhi[lat]) / 2, half = (bhi[lat] - blo[lat]) / 2;
  const hands = [];
  // gear is always in the right hand; facing -z that's +x (Roblox: right = look x up)
  const right = lat === 0 ? -front : front;
  for (const side of (hasGear ? [right] : [-1, 1])) {
    const arm = [...body.keys()].filter(g => { const b = body.get(g); return side * ((b[0][lat] + b[1][lat]) / 2 - mid) > 0.6 * half; });
    if (!arm.length) continue;
    const low = arm.map(g => body.get(g)).reduce((p, b) => (b[0][1] < p[0][1] ? b : p));
    const top = arm.map(g => body.get(g)).reduce((p, b) => (b[1][1] > p[1][1] ? b : p));
    const width = top[1][lat] - top[0][lat];
    // the shoulder joint sits in the middle of the arm's top block (Roblox rigs)
    const shoulder = [0, top[1][1] - width / 2, (top[0][dep] + top[1][dep]) / 2];
    hands.push({ h: [low[0].slice(), [low[1][0], Math.min(low[1][1], low[0][1] + 0.6), low[1][2]]], arm, shoulder });
  }
  const touches = (b, h, pad = 0.2) => [0, 1, 2].every(a => b[0][a] <= h[1][a] + pad && b[1][a] >= h[0][a] - pad);
  const changedV = new Set(), changedN = new Set();
  const union = list => {
    const bs = list.map(g => box(groups.get(g)[0]));
    return [[0, 1, 2].map(a => Math.min(...bs.map(b => b[0][a]))), [0, 1, 2].map(a => Math.max(...bs.map(b => b[1][a])))];
  };
  for (const { h, arm, shoulder } of hands) {
    const neck = shoulder[1] + 0.5 * (h[1][lat] - h[0][lat]);   // above this: hats and hair, never gear
    let tool;
    if (hasGear) {
      // we know a gear is held: whatever is in the right hand is it, whatever its shape
      tool = [...groups.keys()].filter(g => {
        const v = groups.get(g)[0];
        return !body.has(g) && v.size && touches(box(v), h, 0.25) && box(v)[0][1] < neck;
      });
      // plus any parts attached to those (a jar's lid, a bucket's handle...)
      let grew = true;
      while (grew && tool.length) {
        grew = false;
        const u = union(tool);
        for (const [g, [v]] of groups) {
          if (!body.has(g) && !tool.includes(g) && v.size && box(v)[0][1] < neck && touches(box(v), u, 0.05)) { tool.push(g); grew = true; }
        }
      }
    } else {
      // not sure: only things clearly sticking out of the hand (a long staff or sword)
      tool = [...groups.keys()].filter(g => {
        const v = groups.get(g)[0];
        if (body.has(g) || !v.size) return false;
        const b = box(v);
        return touches(b, h) && (b[1][dep] > bhi[dep] + 0.6 || b[0][dep] < blo[dep] - 0.6);
      });
      if (tool.length) {
        const [tlo, thi] = union(tool);
        for (const [g, [v]] of groups) {            // other loose parts of the same gear
          if (body.has(g) || tool.includes(g) || !v.size) continue;
          const b = box(v);
          const outFront = b[0][dep] > bhi[dep] + 0.3 || b[1][dep] < blo[dep] - 0.3;
          const inLine = b[0][1] >= tlo[1] - 0.5 && b[1][1] <= thi[1] + 0.5 && b[0][lat] >= tlo[lat] - 0.5 && b[1][lat] <= thi[lat] + 0.5;
          if (outFront && inLine) tool.push(g);
        }
      }
    }
    if (!tool.length) continue;
    const hc = [0, 1, 2].map(a => (h[0][a] + h[1][a]) / 2);
    const fwd = front;
    // raise the arm to the front and turn the gear with it, around the shoulder (Roblox's grip)
    const mv = new Set(), mn = new Set();
    for (const g of [...tool, ...arm]) { for (const i of groups.get(g)[0]) mv.add(i); for (const i of groups.get(g)[1]) mn.add(i); }
    const piv = shoulder;
    for (const i of mv) {
      const p = V[i], dy = p[1] - piv[1], dd = p[dep] - piv[dep], q = p.slice();
      q[1] = piv[1] + fwd * dd; q[dep] = piv[dep] - fwd * dy; V[i] = q; changedV.add(i);
    }
    for (const i of mn) {
      const n = N[i], q = n.slice();
      q[1] = fwd * n[dep]; q[dep] = -fwd * n[1]; N[i] = q; changedN.add(i);
    }
    // then put the gear just in front of the fist (touching it, centred on the hand),
    //    so it doesn't go through the arm
    const av = new Set(), tv = new Set();
    for (const g of arm) for (const i of groups.get(g)[0]) av.add(i);
    for (const g of tool) for (const i of groups.get(g)[0]) tv.add(i);
    let tip = front > 0 ? -Infinity : Infinity, near = front > 0 ? Infinity : -Infinity;
    let alo = Infinity, ahi = -Infinity, tlo2 = Infinity, thi2 = -Infinity;
    for (const i of av) { const p = V[i]; tip = front > 0 ? Math.max(tip, p[dep]) : Math.min(tip, p[dep]); alo = Math.min(alo, p[lat]); ahi = Math.max(ahi, p[lat]); }
    for (const i of tv) { const p = V[i]; near = front > 0 ? Math.min(near, p[dep]) : Math.max(near, p[dep]); tlo2 = Math.min(tlo2, p[lat]); thi2 = Math.max(thi2, p[lat]); }
    const shiftD = tip + front * 0.02 - near, shiftL = (alo + ahi) / 2 - (tlo2 + thi2) / 2;
    for (const i of tv) { const q = V[i].slice(); q[dep] += shiftD; q[lat] += shiftL; V[i] = q; }
  }
  if (!changedV.size) return { text, bounds: null };
  for (const i of changedV) lines[vline[i]] = `v ${V[i].map(x => x.toFixed(6)).join(' ')}`;
  for (const i of changedN) lines[nline[i]] = `vn ${N[i].map(x => x.toFixed(6)).join(' ')}`;
  const lo = [Infinity, Infinity, Infinity], hi = [-Infinity, -Infinity, -Infinity];
  for (const p of V) for (let a = 0; a < 3; a++) { if (p[a] < lo[a]) lo[a] = p[a]; if (p[a] > hi[a]) hi[a] = p[a]; }
  return { text: lines.join('\n'), bounds: [lo, hi] };
}

// The folder zip. spin = makeSpin() result (avatar_spin.gif + avatar_front.png), optional.
export function buildZip(av, user = 'avatar', script = null, spin = null) {
  const enc = new TextEncoder();
  const dir = `${user}_3d/`;
  const files = [
    { name: `${dir}README.txt`, data: enc.encode(readme(user)) },
    ...(spin ? [{ name: `${dir}avatar_spin.gif`, data: spin.gif }] : []),
    ...(spin?.front ? [{ name: `${dir}avatar_front.png`, data: spin.front }] : []),
    { name: `${dir}avatar.obj`, data: av.obj },
    { name: `${dir}avatar.mtl`, data: av.mtl },
    { name: `${dir}camera.json`, data: av.camera },
    ...(script ? [{ name: `${dir}render_gif.py`, data: enc.encode(script) }] : []),
    ...av.textures.map(t => ({ name: dir + t.name, data: t.data })),
  ];
  return { zip: makeZip(files), files };
}
