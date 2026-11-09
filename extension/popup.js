import { userByName, userById, headshotUrl, fetchAvatar, buildZip, avatarHasGear } from './lib.js';
import { makeSpin } from './spin.js';

const $ = id => document.getElementById(id);
const q = $('q'), card = $('card'), go = $('go'), fill = go.querySelector('.fill'), label = $('label'), msg = $('msg');
let user = null;
let cache = null;   // { id, av, spin } so a second click doesn't download or render again

function say(text, err = false) { msg.textContent = text; msg.classList.toggle('err', err); }
function progress(p) { fill.style.transform = `scaleX(${p})`; }
const nextPaint = () => new Promise(r => setTimeout(r, 0));

async function show(u) {
  user = u;
  $('dn').textContent = u.displayName;
  $('un').textContent = '@' + u.name;
  $('pic').src = (await headshotUrl(u.id).catch(() => null)) || '';
  card.classList.add('on');
  go.disabled = false;
  say('');
}

$('search').addEventListener('submit', async e => {
  e.preventDefault();
  const name = q.value.trim();
  if (!name) return;
  try { await show(await userByName(name)); }
  catch (err) { user = null; card.classList.remove('on'); go.disabled = true; say(err.message, true); }
});

// The avatar-3d endpoint needs the user's Roblox login.
async function avatar3d(id) {
  const url = `https://thumbnails.roblox.com/v1/users/avatar-3d?userId=${id}`;
  // 1) straight from the extension (works when the browser sends the Roblox cookie)
  try {
    for (let i = 0; i < 15; i++) {
      const r = await fetch(url, { credentials: 'include' });
      if (!r.ok) break;
      const j = await r.json();
      if (j.state === 'Completed' && j.imageUrl) return j;
      await new Promise(res => setTimeout(res, 1500));
    }
  } catch { /* fall through */ }
  // 2) ask from inside an open roblox.com tab, exactly like the site itself does
  const tabs = await chrome.tabs.query({ url: 'https://*.roblox.com/*' });
  if (!tabs.length) throw new Error('Open roblox.com and log in, then try again');
  const [{ result }] = await chrome.scripting.executeScript({
    target: { tabId: tabs[0].id },
    world: 'MAIN',
    args: [url],
    func: async url => {
      for (let i = 0; i < 15; i++) {
        const r = await fetch(url, { credentials: 'include' });
        if (!r.ok) return { error: r.status };
        const j = await r.json();
        if (j.state === 'Completed' && j.imageUrl) return j;
        await new Promise(res => setTimeout(res, 1500));
      }
      return { error: 'pending' };
    },
  });
  if (!result || result.error) {
    throw new Error(result?.error === 401 || result?.error === 403 ? 'Log in to roblox.com first' : 'Roblox is still rendering it, try again');
  }
  return result;
}

// PNG in/out with the browser's own image code
async function decodePng(bytes) {
  const bmp = await createImageBitmap(new Blob([bytes]), { premultiplyAlpha: 'none', colorSpaceConversion: 'none' });
  const c = new OffscreenCanvas(bmp.width, bmp.height).getContext('2d', { willReadFrequently: true });
  c.drawImage(bmp, 0, 0);
  return { w: bmp.width, h: bmp.height, data: c.getImageData(0, 0, bmp.width, bmp.height).data };
}
async function encodePng(rgba, w, h) {
  const cv = new OffscreenCanvas(w, h);
  cv.getContext('2d').putImageData(new ImageData(rgba, w, h), 0, 0);
  return new Uint8Array(await (await cv.convertToBlob({ type: 'image/png' })).arrayBuffer());
}

async function getAll() {
  if (cache?.id === user.id) { progress(0.95); return cache; }
  label.textContent = 'Getting avatar';
  const info = await avatar3d(user.id);
  // only ever follow the file list link to Roblox's own file servers
  if (!/^https:\/\/t[0-7]\.rbxcdn\.com\/[A-Za-z0-9-]{8,80}$/.test(info.imageUrl)) throw new Error('Roblox answered strangely, try again');
  const meta = await (await fetch(info.imageUrl, { credentials: 'omit' })).json();
  progress(0.1);
  label.textContent = 'Downloading';
  const av = await fetchAvatar(meta, fetch, (d, t) => progress(0.1 + 0.45 * d / t), await avatarHasGear(user.id));
  label.textContent = 'Making GIF';
  await nextPaint();
  const spin = await makeSpin(av, { decodePng, encodePng, onFrame: (n, t) => { progress(0.55 + 0.4 * n / t); return nextPaint(); } });
  cache = { id: user.id, av, spin };
  return cache;
}

async function save(bytes, type, filename) {
  const blobUrl = URL.createObjectURL(new Blob([bytes], { type }));
  await chrome.downloads.download({ url: blobUrl, filename });
  setTimeout(() => URL.revokeObjectURL(blobUrl), 60000);
}

go.addEventListener('click', async () => {
  if (!user || go.classList.contains('busy')) return;
  go.classList.add('busy'); go.disabled = true; progress(0.04); say('');
  try {
    const { av, spin } = await getAll();
    const script = await (await fetch('render_gif.py')).text();
    await save(buildZip(av, user.name, script, spin).zip, 'application/zip', `${user.name}_3d.zip`);
    progress(1);
    label.textContent = 'Saved';
  } catch (err) {
    progress(0);
    label.textContent = 'Download 3D';
    say(err.message || 'Something went wrong', true);
  } finally {
    go.classList.remove('busy'); go.disabled = !user;
    setTimeout(() => { if (label.textContent === 'Saved') { label.textContent = 'Download 3D'; progress(0); } }, 2500);
  }
});

// Help view
const home = $('home'), help = $('helpview');
function view(show, hide, back) {
  hide.classList.remove('on', 'back');
  show.classList.toggle('back', back);
  show.classList.add('on');
}
$('help').addEventListener('click', () => view(help, home, false));
$('back').addEventListener('click', () => view(home, help, true));

// Opened on a profile page? Load that avatar straight away.
(async () => {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  const m = tab?.url?.match(/roblox\.com\/(?:[a-z]{2}\/)?users\/(\d+)/);
  if (m) {
    try { const u = await userById(m[1]); q.value = u.name; await show(u); return; } catch { /* ignore */ }
  }
  q.focus();
})();
