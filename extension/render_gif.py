"""Turn this Roblox avatar into a 360-degree spinning GIF.

The extension already puts avatar_spin.gif in the zip; use this for other sizes.
Needs only Python 3 + numpy + Pillow (no Blender, no GPU, no internet).

    python render_gif.py                      # avatar_spin.gif (+ .webp, avatar_front.png)
    python render_gif.py --size 300 --frames 24   # faster
    python render_gif.py --size 720 --frames 48   # nicer, slower
"""
import argparse, io, json, math, os, sys, time
import numpy as np
from PIL import Image

try:
    HERE = os.path.dirname(os.path.abspath(__file__))
except NameError:          # run with exec() in a notebook
    HERE = os.getcwd()
if not os.access(HERE, os.W_OK):
    HERE = os.getcwd()

def read_bytes(name):
    with open(os.path.join(HERE, name), "rb") as f:
        return f.read()


def exists(name):
    return os.path.exists(os.path.join(HERE, name))


def read_text(name):
    return read_bytes(name).decode("utf-8", errors="ignore").splitlines(keepends=True)
# older Pillow (some AI sandboxes) has these as plain constants
MEDIANCUT = getattr(getattr(Image, "Quantize", None), "MEDIANCUT", 0)
NO_DITHER = getattr(getattr(Image, "Dither", None), "NONE", 0)
LANCZOS = getattr(getattr(Image, "Resampling", None), "LANCZOS", getattr(Image, "LANCZOS", 1))


# ---------------------------------------------------------------- loading
def load_obj(name):
    V, VT, VN = [], [], []
    tris, mats = [], []
    cur = None
    if True:
        for line in read_text(name):
            if line.startswith("v "):
                V.append([float(x) for x in line.split()[1:4]])
            elif line.startswith("vt "):
                VT.append([float(x) for x in line.split()[1:3]])
            elif line.startswith("vn "):
                VN.append([float(x) for x in line.split()[1:4]])
            elif line.startswith("usemtl "):
                cur = line.split()[1]
            elif line.startswith("f "):
                idx = []
                for t in line.split()[1:]:
                    p = (t.split("/") + ["", ""])[:3]
                    idx.append([int(p[0]) - 1, int(p[1]) - 1 if p[1] else -1, int(p[2]) - 1 if p[2] else -1])
                for k in range(1, len(idx) - 1):
                    tris.append([idx[0], idx[k], idx[k + 1]]); mats.append(cur)
    return np.array(V, np.float64), np.array(VT or [[0, 0]], np.float64), np.array(VN or [[0, 1, 0]], np.float64), np.array(tris), mats


def load_mtl(name):
    mats, cur = {}, None
    if True:
        for line in read_text(name):
            p = line.split()
            if not p:
                continue
            if p[0] == "newmtl":
                cur = p[1]; mats[cur] = {}
            elif cur and len(p) > 1:
                mats[cur][p[0]] = p[1:]
    return mats


_tex_cache = {}
def load_tex(name):
    if name not in _tex_cache:
        if exists(name):
            _tex_cache[name] = np.asarray(Image.open(io.BytesIO(read_bytes(name))).convert("RGBA"), np.float32)
        else:
            _tex_cache[name] = None
    return _tex_cache[name]


def bilinear(t, u, v):
    h, w = t.shape[:2]
    u = np.mod(u, w); v = np.clip(v, 0, h - 1)
    x0 = np.floor(u).astype(int); y0 = np.floor(v).astype(int)
    fx = (u - x0)[:, None]; fy = (v - y0)[:, None]
    x1 = (x0 + 1) % w; y1 = np.minimum(y0 + 1, h - 1)
    return (t[y0, x0] * (1 - fx) * (1 - fy) + t[y0, x1] * fx * (1 - fy)
            + t[y1, x0] * (1 - fx) * fy + t[y1, x1] * fx * fy)


# ---------------------------------------------------------------- scene
class Scene:
    def __init__(self):
        V, VT, VN, T, M = load_obj("avatar.obj")
        mtl = load_mtl("avatar.mtl")
        self.P = V[T[..., 0]]
        self.N = VN[np.where(T[..., 2] >= 0, T[..., 2], 0)].astype(np.float32)
        self.mat_names = sorted(set(m or "" for m in M))
        self.mid = np.array([self.mat_names.index(m or "") for m in M])
        self.color, self.alpha, self.glow, self.uv = [], [], [], np.zeros(T.shape[:2] + (2,), np.float32)
        uv = VT[np.where(T[..., 1] >= 0, T[..., 1], 0)]
        for k, name in enumerate(self.mat_names):
            m = mtl.get(name, {})
            tex = load_tex(m["map_Kd"][0]) if "map_Kd" in m else None
            if tex is None:   # plain colour material
                kd = [float(x) for x in m.get("Kd", ["0.8", "0.8", "0.8"])]
                tex = np.ones((2, 2, 4), np.float32) * 255; tex[..., :3] = np.array(kd) * 255
            glow = load_tex(m["map_Ke"][0]) if "map_Ke" in m else None
            self.color.append(tex); self.glow.append(glow)
            sel = self.mid == k
            h, w = tex.shape[:2]
            self.uv[sel, :, 0] = uv[sel, :, 0] * w - 0.5
            self.uv[sel, :, 1] = (1 - uv[sel, :, 1]) * h - 0.5

    def render(self, eye, target, fov, size, ss):
        S = size * ss
        f = target - eye; f /= np.linalg.norm(f)
        r = np.cross(f, [0, 1, 0]); r /= np.linalg.norm(r)
        u = np.cross(r, f)
        Pc = (self.P - eye) @ np.stack([r, u, -f]).T
        foc = (S / 2) / math.tan(math.radians(fov) / 2)
        zc = -Pc[..., 2]
        sx = S / 2 + foc * Pc[..., 0] / zc
        sy = S / 2 - foc * Pc[..., 1] / zc
        depth = np.full((S, S), np.inf, np.float32)
        tid = np.full((S, S), -1, np.int32)
        b0 = np.zeros((S, S), np.float32); b1 = np.zeros((S, S), np.float32)
        for i in range(len(self.P)):
            z = zc[i]
            if (z <= 0.05).any():
                continue
            x0, x1, x2 = sx[i]; y0, y1, y2 = sy[i]
            minx = max(int(min(x0, x1, x2)), 0); maxx = min(int(max(x0, x1, x2)) + 1, S - 1)
            miny = max(int(min(y0, y1, y2)), 0); maxy = min(int(max(y0, y1, y2)) + 1, S - 1)
            if minx > maxx or miny > maxy:
                continue
            den = (y1 - y2) * (x0 - x2) + (x2 - x1) * (y0 - y2)
            if abs(den) < 1e-9:
                continue
            gx, gy = np.meshgrid(np.arange(minx, maxx + 1) + 0.5, np.arange(miny, maxy + 1) + 0.5)
            w0 = ((y1 - y2) * (gx - x2) + (x2 - x1) * (gy - y2)) / den
            w1 = ((y2 - y0) * (gx - x2) + (x0 - x2) * (gy - y2)) / den
            w2 = 1 - w0 - w1
            inside = (w0 >= 0) & (w1 >= 0) & (w2 >= 0)
            if not inside.any():
                continue
            iz = w0 / z[0] + w1 / z[1] + w2 / z[2]
            zz = 1 / np.maximum(iz, 1e-9)
            p0 = w0 / z[0] * zz; p1 = w1 / z[1] * zz
            cur = depth[miny:maxy + 1, minx:maxx + 1]
            ok = inside & (zz < cur)
            if not ok.any():
                continue
            # alpha test: see-through parts of hair / accessories
            tex = self.color[self.mid[i]]
            uvp = self.uv[i]
            uu = p0 * uvp[0, 0] + p1 * uvp[1, 0] + (1 - p0 - p1) * uvp[2, 0]
            vv = p0 * uvp[0, 1] + p1 * uvp[1, 1] + (1 - p0 - p1) * uvp[2, 1]
            th, tw = tex.shape[:2]
            a = tex[np.clip(np.round(vv).astype(int), 0, th - 1), np.round(uu).astype(int) % tw, 3]
            ok &= a > 100
            if not ok.any():
                continue
            cur[ok] = zz[ok]
            tid[miny:maxy + 1, minx:maxx + 1][ok] = i
            b0[miny:maxy + 1, minx:maxx + 1][ok] = p0[ok]
            b1[miny:maxy + 1, minx:maxx + 1][ok] = p1[ok]
        img = np.zeros((S, S, 4), np.float32)
        m = tid >= 0
        t = tid[m]; p0 = b0[m][:, None]; p1 = b1[m][:, None]; p2 = 1 - p0 - p1
        uv = self.uv[t, 0] * p0 + self.uv[t, 1] * p1 + self.uv[t, 2] * p2
        n = self.N[t, 0] * p0 + self.N[t, 1] * p1 + self.N[t, 2] * p2
        n /= np.maximum(np.linalg.norm(n, axis=1, keepdims=True), 1e-6)
        col = np.zeros((len(t), 3), np.float32); emis = np.zeros((len(t), 3), np.float32)
        mid = self.mid[t]
        for k in range(len(self.mat_names)):
            sel = mid == k
            if not sel.any():
                continue
            tex = self.color[k]
            col[sel] = bilinear(tex, uv[sel, 0], uv[sel, 1])[:, :3]
            g = self.glow[k]
            if g is not None:
                sx_, sy_ = g.shape[1] / tex.shape[1], g.shape[0] / tex.shape[0]
                emis[sel] = bilinear(g, uv[sel, 0] * sx_, uv[sel, 1] * sy_)[:, :3]
        key = -f + 0.55 * u - 0.35 * r; key /= np.linalg.norm(key)
        fill = -f - 0.3 * u + 0.4 * r; fill /= np.linalg.norm(fill)
        lam = 0.62 + 0.34 * np.clip(n @ key, 0, 1) + 0.08 * np.clip(n @ fill, 0, 1)
        img[m, :3] = np.clip(col * lam[:, None] + emis * 0.9, 0, 255); img[m, 3] = 255
        im = Image.fromarray(img.astype(np.uint8), "RGBA")
        return np.asarray(im.resize((size, size), LANCZOS))


# ---------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--size", type=int, default=400, help="pixels (default 400)")
    ap.add_argument("--frames", type=int, default=36, help="frames per turn (default 36)")
    ap.add_argument("--ss", type=int, default=2, help="anti-aliasing 1-3 (default 2)")
    ap.add_argument("--ms", type=int, default=70, help="milliseconds per frame (default 70)")
    ap.add_argument("--out", default="avatar_spin", help="output name without extension")
    a, _ = ap.parse_known_args()      # tolerate notebook / %run arguments

    t0 = time.time()
    scene = Scene()
    cam = json.loads(read_bytes("camera.json"))
    lo = np.array([cam["aabb"]["min"][k] for k in "xyz"]); hi = np.array([cam["aabb"]["max"][k] for k in "xyz"])
    center = (lo + hi) / 2
    fov = cam["camera"]["fov"]
    d = np.array([cam["camera"]["direction"][k] for k in "xyz"])
    elev = math.asin(max(-0.6, min(0.6, d[1] / np.linalg.norm(d))))      # Roblox's own camera tilt
    radius = np.linalg.norm(hi - lo) / 2
    dist = radius / math.sin(math.radians(fov) / 2) * 1.02                   # whole avatar always in frame

    frames = []
    for i in range(a.frames):
        yaw = 2 * math.pi * i / a.frames
        off = np.array([math.sin(yaw) * math.cos(elev), math.sin(elev), -math.cos(yaw) * math.cos(elev)])
        frames.append(scene.render(center + off * dist, center, fov, a.size, a.ss))
        print(f"frame {i + 1}/{a.frames}", flush=True)

    # crop every frame to the same box around the avatar
    al = np.max([f[..., 3] for f in frames], 0); ys, xs = np.where(al > 0)
    pad = max(4, a.size // 60)
    y0, y1 = max(ys.min() - pad, 0), min(ys.max() + pad + 1, a.size)
    x0, x1 = max(xs.min() - pad, 0), min(xs.max() + pad + 1, a.size)
    rgba = [Image.fromarray(np.ascontiguousarray(f[y0:y1, x0:x1]), "RGBA") for f in frames]

    rgba[0].save(os.path.join(HERE, "avatar_front.png"))
    # GIF: one shared palette (no flicker), index 255 = transparent
    W, H = rgba[0].size
    strip = Image.new("RGB", (W, H * len(rgba)))
    for k, f in enumerate(rgba):
        strip.paste(f.convert("RGB"), (0, k * H))
    pal = strip.quantize(255, method=MEDIANCUT)
    idx = []
    for f in rgba:
        px = np.array(f.convert("RGB").quantize(palette=pal, dither=NO_DITHER), np.uint8)
        px[np.asarray(f)[..., 3] < 128] = 255
        idx.append(px)
    gif_path = os.path.join(HERE, a.out + ".gif")
    write_gif(gif_path, idx, (pal.getpalette() + [0] * 768)[:765] + [0, 0, 0], a.ms)
    print("saved", gif_path)
    try:   # sharper, smooth edges; skipped if this Pillow has no WebP
        webp = os.path.join(HERE, a.out + ".webp")
        rgba[0].save(webp, save_all=True, append_images=rgba[1:], duration=a.ms, loop=0, lossless=True)
        print("saved", webp)
    except Exception:
        pass
    print(f"done in {time.time() - t0:.0f}s")


def _lzw(data, min_size=8):
    """GIF LZW compression of a bytes object of palette indices, returns the packed code stream."""
    clear, eoi = 1 << min_size, (1 << min_size) + 1
    out = bytearray()
    cur = nbits = 0
    size = min_size + 1
    table = {}
    nxt = eoi + 1

    def emit(code):
        nonlocal cur, nbits
        cur |= code << nbits
        nbits += size
        while nbits >= 8:
            out.append(cur & 255)
            cur >>= 8
            nbits -= 8

    emit(clear)
    prefix = data[0]
    for b in data[1:]:
        key = (prefix << 8) | b
        c = table.get(key)
        if c is not None:
            prefix = c
            continue
        emit(prefix)
        if nxt == 4096:                       # table full: start over
            emit(clear)
            table.clear()
            nxt = eoi + 1
            size = min_size + 1
        else:
            if nxt >= (1 << size):
                size += 1
            table[key] = nxt
            nxt += 1
        prefix = b
    emit(prefix)
    emit(eoi)
    if nbits:
        out.append(cur & 255)
    return bytes(out)


class _Keep:
    """lets write_gif take an open file / BytesIO too (and leaves it open)"""
    def __init__(self, f): self.f = f
    def __enter__(self): return self.f
    def __exit__(self, *a): return False


def write_gif(path, frames, palette, ms):
    """Write an animated GIF of full frames (no frame trimming, which made some Pillow versions
    erase dark parts like horns), one shared 256-colour palette, index 255 transparent."""
    import struct
    H, W = frames[0].shape
    with (open(path, "wb") if isinstance(path, str) else _Keep(path)) as fh:
        fh.write(b"GIF89a" + struct.pack("<HHBBB", W, H, 0xF7, 255, 0) + bytes(palette))
        fh.write(b"\x21\xFF\x0BNETSCAPE2.0\x03\x01\x00\x00\x00")           # loop forever
        delay = max(2, round(ms / 10))
        for px in frames:
            # graphic control: dispose to background, transparent index 255
            fh.write(b"\x21\xF9\x04" + bytes([(2 << 2) | 1]) + struct.pack("<H", delay) + b"\xFF\x00")
            fh.write(b"\x2C" + struct.pack("<HHHHB", 0, 0, W, H, 0) + b"\x08")
            data = _lzw(np.ascontiguousarray(px).tobytes())
            for i in range(0, len(data), 255):
                chunk = data[i:i + 255]
                fh.write(bytes([len(chunk)]) + chunk)
            fh.write(b"\x00")
        fh.write(b"\x3B")



if __name__ == "__main__":
    main()
