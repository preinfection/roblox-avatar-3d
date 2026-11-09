// End-to-end test of the extension's core logic (everything except the logged-in step,
// which is fed from an avatar-3d result saved earlier): lookups, download, GIF render, zip.
//   node test/run.js <saved avatar-3d json> <out.zip> <username> [userId]
import { readFileSync, writeFileSync } from 'node:fs';
import { userByName, userById, headshotUrl, fetchAvatar, buildZip, cdnUrl, avatarHasGear } from '../extension/lib.js';
import { makeSpin } from '../extension/spin.js';
import { decodePng, encodePng } from './png.js';

const [metaPath, outZip] = process.argv.slice(2);
const u = await userByName(process.argv[4] || 'builderman');
console.log('userByName', u);
console.log('userById ', await userById(u.id));
console.log('headshot ', (await headshotUrl(u.id))?.slice(0, 60));
const meta = JSON.parse(readFileSync(metaPath, 'utf8'));
console.log('cdn host for obj', cdnUrl(meta.obj));
let last = 0;
const uid = process.argv[5] ? Number(process.argv[5]) : u.id;
const gear = await avatarHasGear(uid);
console.log('has gear', gear);
const av = await fetchAvatar(meta, fetch, (d, t) => { last = d / t; }, gear);
console.log('download progress end', last);

const t0 = Date.now();
let frames = 0;
const spin = await makeSpin(av, { decodePng, encodePng, onFrame: n => { frames = n; } });
console.log(`gif ${spin.width}x${spin.height}, ${frames} frames, ${spin.gif.length} bytes, front png ${spin.front.length} bytes, ${((Date.now() - t0) / 1000).toFixed(1)}s`);

const script = readFileSync(new URL('../extension/render_gif.py', import.meta.url), 'utf8');
const { zip, files } = buildZip(av, u.name, script, spin);
writeFileSync(outZip, zip);
console.log('zip files', files.length, 'zip bytes', zip.length);
console.log(files.map(f => f.name).join('\n'));
