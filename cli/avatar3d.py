"""avatar3d - download any Roblox avatar as a 3D model + spinning GIF. No Roblox login needed.

    python avatar3d.py builderman
    python avatar3d.py builderman --size 720 --frames 48      # bigger, smoother GIF

Needs Python 3.8+ with numpy and Pillow  (pip install numpy pillow).
"""
import argparse, io, json, math, os, sys, time, zipfile
import urllib.request, urllib.error
from concurrent.futures import ThreadPoolExecutor

API = os.environ.get("AVATAR3D_API", "https://mathium.xyz/api/avatar3d")
UA = {"User-Agent": "avatar3d-cli/1.0"}

try:
    import numpy as np
    from PIL import Image
except ImportError:
    np = Image = None

# ---------------------------------------------------------------- terminal
def _vt():
    """turn on colours in the Windows console; returns True if escape codes work"""
    good = os.name != "nt"
    if os.name == "nt":
        try:
            import ctypes
            k = ctypes.windll.kernel32
            h = k.GetStdHandle(-11)
            mode = ctypes.c_uint32()
            if k.GetConsoleMode(h, ctypes.byref(mode)):
                good = bool(k.SetConsoleMode(h, mode.value | 0x4))
        except Exception:
            pass
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    return good


def clear(vt):
    """fresh, empty window"""
    if vt or os.environ.get("FORCE_COLOR"):
        sys.stdout.write("[2J[3J[H"); sys.stdout.flush()
    elif sys.stdout.isatty():
        os.system("cls" if os.name == "nt" else "clear")

C = {"dim": "\x1b[90m", "b": "\x1b[1m", "ok": "\x1b[32m", "err": "\x1b[31m", "w": "\x1b[97m", "0": "\x1b[0m"}
LIVE = (sys.stdout.isatty() or bool(os.environ.get("FORCE_COLOR"))) and not os.environ.get("NO_COLOR")
if not LIVE:
    C = {k: "" for k in C}
LABEL = 15
GREEN = (22, 198, 12)
RED = (226, 35, 26)        # Roblox red, while it works
LOGO = "◘"                 # text stand-in for the logo, for terminals that can't show pictures
SPIN = ["◘", "◆"]

# The old Roblox "R" logo as an outline (unit box): outer shape + the triangle hole.
R_OUTER = [[0.796, 0.0], [0.057, 0.094], [0.087, 0.49], [0.098, 0.845], [0.23, 0.865], [0.394, 0.906], [0.368, 0.768], [0.301, 0.587], [0.312, 0.584], [0.515, 0.997], [0.694, 0.953], [0.94, 0.912], [0.541, 0.551], [0.544, 0.54], [0.808, 0.569], [0.922, 0.452]]
R_HOLE = [[0.295, 0.27], [0.295, 0.358], [0.468, 0.328], [0.477, 0.32], [0.477, 0.296], [0.468, 0.29], [0.306, 0.264]]

def r_logo(size, turn=0.0, color=(255, 255, 255), ss=8):
    """the R as a small RGBA picture, turned `turn` degrees"""
    from PIL import ImageDraw
    S = size * ss
    im = Image.new("L", (S, S), 0); d = ImageDraw.Draw(im)
    t = math.radians(turn); k = S * 0.86
    def pts(poly):
        return [(S / 2 + ((x - .5) * math.cos(t) - (y - .5) * math.sin(t)) * k,
                 S / 2 + ((x - .5) * math.sin(t) + (y - .5) * math.cos(t)) * k) for x, y in poly]
    d.polygon(pts(R_OUTER), fill=255); d.polygon(pts(R_HOLE), fill=0)
    a = im.resize((size, size), getattr(getattr(Image, "Resampling", Image), "LANCZOS"))
    pic = Image.new("RGBA", (size, size), tuple(color) + (0,)); pic.putalpha(a)
    return pic

def sixel(img, bg=(12, 12, 12)):
    """a small picture as sixel graphics (Windows Terminal 1.22+ draws these): see-through
    background, 3 shades on the edges so it isn't jagged"""
    W, H = img.size
    px = img.load()
    shades, grid = {}, [[None] * W for _ in range(H)]
    for y in range(H):
        for x in range(W):
            r, g, b, a = px[x, y]
            if a < 50:
                continue
            k = 1.0 if a > 215 else 0.66 if a > 130 else 0.36
            c = tuple(round(bg[i] + (v - bg[i]) * k) for i, v in enumerate((r, g, b)))
            grid[y][x] = shades.setdefault(c, len(shades))
    out = ["\x1bP0;1;0q", f'"1;1;{W};{H}']
    out += [f"#{n};2;{round(c[0] / 2.55)};{round(c[1] / 2.55)};{round(c[2] / 2.55)}" for c, n in shades.items()]
    for band in range(0, H, 6):
        first = True
        for c, n in shades.items():
            row = []
            for x in range(W):
                bits = 0
                for k in range(6):
                    if band + k < H and grid[band + k][x] == n:
                        bits |= 1 << k
                row.append(chr(63 + bits))
            if all(ch == "?" for ch in row):
                continue
            s, i = [], 0
            while i < W:
                j = i
                while j < W and row[j] == row[i]:
                    j += 1
                s.append(f"!{j - i}{row[i]}" if j - i > 3 else row[i] * (j - i)); i = j
            out.append(("" if first else "$") + f"#{n}" + "".join(s)); first = False
        out.append("-")
    out.append("\x1b\\")
    return "".join(out)

# pictures only in Windows Terminal (it understands sixel); anything else gets the text icon
PICS = None
def _pics():
    global PICS
    if LIVE and Image is not None and os.environ.get("WT_SESSION") and not os.environ.get("AVATAR3D_TEXT_ICONS"):
        PICS = {"spin": [sixel(r_logo(20, a, RED)) for a in range(0, 360, 30)], "done": sixel(r_logo(20, 0, GREEN))}

import threading
_lock = threading.Lock()
_now = None                # the step being worked on: (label, done, total)
_tick = 0

def out(s=""):
    with _lock:
        sys.stdout.write(s + "\n"); sys.stdout.flush()

def _line(icon_text, icon_pic, rest):
    """write a step line; with pictures the icon is drawn over two blank cells"""
    if PICS:
        # icon first (so a long line that wraps can't move it), then the text after it
        sys.stdout.write("\r\x1b[K\x1b[2C\x1b7" + icon_pic + "\x1b8\r\x1b[5C" + rest)
    else:
        sys.stdout.write("\r\x1b[K" + "  " + icon_text + " " + rest)
    sys.stdout.flush()

def _draw(width=18):
    label, done, total = _now
    n = round(width * done / max(total, 1))
    rest = f"{label:<{LABEL}} {'━' * n}{C['dim']}{'─' * (width - n)}  {done}/{total}{C['0']}"
    _line(f"{C['err']}{SPIN[_tick % len(SPIN)]}{C['0']}", PICS and PICS["spin"][_tick % len(PICS["spin"])], rest)

def _spinner():
    global _tick
    while True:
        time.sleep(0.12)
        with _lock:
            if _now is not None:
                _tick += 1; _draw()

_hidden = False

def show_cursor():
    global _hidden
    if _hidden:
        sys.stdout.write("\x1b[?25h"); sys.stdout.flush(); _hidden = False

def bar(label, done, total):
    global _now, _hidden
    with _lock:
        if LIVE and not _hidden:
            sys.stdout.write("\x1b[?25l"); _hidden = True    # no blinking cursor while it works
        if _now is None and PICS:
            sys.stdout.write("\n\x1b[1A")      # keep a row below, so a picture never scrolls the screen
        _now = (label, done, total); _draw()

def ok(label, text=""):
    global _now
    with _lock:
        _now = None
        _line(f"{C['ok']}{LOGO}{C['0']}", PICS and PICS["done"], f"{label:<{LABEL}} {C['dim']}{text}{C['0']}")
        sys.stdout.write("\n"); sys.stdout.flush()

def fail(text):
    global _now
    with _lock:
        _now = None
        sys.stdout.write(f"\r\x1b[K  {C['err']}✗ {text}{C['0']}\n\n"); sys.stdout.flush()

def mb(n):
    return f"{n / 1e6:.1f} MB"

# ---------------------------------------------------------------- where to save
def _browser_dir():
    """the default browser's download folder (Windows): Brave / Chrome / Edge / Firefox"""
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\Shell\Associations\UrlAssociations\https\UserChoice") as k:
            prog = winreg.QueryValueEx(k, "ProgId")[0].lower()
    except Exception:
        return None
    local, roaming = os.environ.get("LOCALAPPDATA", ""), os.environ.get("APPDATA", "")
    chromium = {"brave": r"BraveSoftware\Brave-Browser\User Data", "chrome": r"Google\Chrome\User Data", "msedge": r"Microsoft\Edge\User Data"}
    for key, sub in chromium.items():
        if prog.startswith(key):
            root = os.path.join(local, sub)
            try:
                with open(os.path.join(root, "Local State"), encoding="utf-8") as f:
                    prof = json.load(f).get("profile", {}).get("last_used") or "Default"
            except Exception:
                prof = "Default"
            try:
                with open(os.path.join(root, prof, "Preferences"), encoding="utf-8") as f:
                    pref = json.load(f)
            except Exception:
                return None
            # its download folder, or (when it asks every time) the last folder you saved to
            return (pref.get("download") or {}).get("default_directory") or (pref.get("savefile") or {}).get("default_directory")
    if prog.startswith("firefox"):
        import glob, re
        for prefs in glob.glob(os.path.join(roaming, "Mozilla", "Firefox", "Profiles", "*", "prefs.js")):
            with open(prefs, encoding="utf-8", errors="ignore") as f:
                t = f.read()
            m = re.search(r'user_pref\("browser\.download\.dir",\s*"(.*?)"\)', t)
            if m and 'browser.download.folderList", 2' in t:
                return m.group(1).replace("\\\\", "\\")
    return None


def _windows_downloads():
    """Windows' own Downloads folder, wherever it was moved to"""
    try:
        import ctypes, uuid
        class GUID(ctypes.Structure):
            _fields_ = [("a", ctypes.c_uint32), ("b", ctypes.c_uint16), ("c", ctypes.c_uint16), ("d", ctypes.c_ubyte * 8)]
        g = GUID()
        ctypes.memmove(ctypes.byref(g), uuid.UUID("374DE290-123F-4565-9164-13E4CA9D8F38").bytes_le, 16)
        p = ctypes.c_wchar_p()
        if ctypes.windll.shell32.SHGetKnownFolderPath(ctypes.byref(g), 0x4000, None, ctypes.byref(p)) == 0:
            path = p.value
            ctypes.windll.ole32.CoTaskMemFree(p)
            return path
    except Exception:
        pass
    return None


def downloads_dir():
    picks = []
    if os.name == "nt":
        picks += [_browser_dir(), _windows_downloads()]
    elif sys.platform.startswith("linux"):
        try:
            import subprocess
            picks.append(subprocess.run(["xdg-user-dir", "DOWNLOAD"], capture_output=True, text=True, timeout=5).stdout.strip())
        except Exception:
            pass
    picks.append(os.path.join(os.path.expanduser("~"), "Downloads"))
    for d in picks:
        if d and os.path.isdir(d):
            return d
    return os.getcwd()


def free_name(folder, name):
    """name.zip, or name (2).zip ... like a browser: never overwrite"""
    base, ext = os.path.splitext(name)
    path, n = os.path.join(folder, name), 2
    while os.path.exists(path):
        path = os.path.join(folder, f"{base} ({n}){ext}")
        n += 1
    return path


# ---------------------------------------------------------------- roblox
def http(url, data=None):
    req = urllib.request.Request(url, data=data, headers={**UA, **({"Content-Type": "application/json"} if data else {})})
    try:
        with urllib.request.urlopen(req, timeout=40) as r:
            body = r.read()
            # Roblox's file server sends some files gzipped (browsers unpack that on their own)
            if r.headers.get("Content-Encoding", "").lower() == "gzip" or body[:2] == b"\x1f\x8b":
                import gzip
                body = gzip.decompress(body)
            return r.status, body
    except urllib.error.HTTPError as e:
        return e.code, e.read()

def user_by_name(name):
    s, b = http("https://users.roblox.com/v1/usernames/users", json.dumps({"usernames": [name], "excludeBannedUsers": False}).encode())
    if s != 200:
        raise RuntimeError("couldn't reach Roblox")
    d = json.loads(b).get("data") or []
    if not d:
        raise RuntimeError(f"no Roblox user called {name}")
    return d[0]

def file_list(uid):
    for _ in range(15):
        s, b = http(f"{API}?userId={uid}")
        try:
            j = json.loads(b)
        except Exception:
            j = {}
        if s == 200 and j.get("meta"):
            return j["meta"]
        if s == 202:
            time.sleep(2); continue
        raise RuntimeError(j.get("error") or f"service error {s}")
    raise RuntimeError("Roblox is still making the 3D model, try again in a minute")

def avatar_has_gear(uid):
    """True / False from the avatar's public item list; None if Roblox didn't answer"""
    try:
        s, b = http(f"https://avatar.roblox.com/v1/users/{uid}/avatar")
        if s == 200:
            return any((a.get("assetType") or {}).get("name") == "Gear" for a in json.loads(b).get("assets", []))
    except Exception:
        pass
    return None

def cdn(h):
    i = 31
    for ch in h[:38]:
        i ^= ord(ch)
    return [f"https://t{i % 8}.rbxcdn.com/{h}"] + [f"https://t{n}.rbxcdn.com/{h}" for n in range(8) if n != i % 8]

def fetch_hash(h):
    for u in cdn(h):
        try:
            s, b = http(u)
            if s == 200:
                return b
        except Exception:
            pass
    raise RuntimeError("a file is missing on Roblox's servers")

def texture_names(mtl, hashes):
    """name textures by what they are: body / accessory_N (+ _normal, _roughness, _glow)"""
    kind = {"map_Kd": "", "map_Ka": "", "map_d": "", "map_Bump": "_normal", "map_bump": "_normal", "map_Ns": "_roughness", "map_Ke": "_glow"}
    names, taken, acc, bodies, mat = {}, set(), {}, 0, ""
    for line in mtl.splitlines():
        p = line.split()
        if not p:
            continue
        if p[0] == "newmtl":
            mat = p[1] if len(p) > 1 else ""; continue
        if p[0] not in kind or len(p) < 2 or p[1] in names:
            continue
        if mat.lower().startswith("player"):
            bodies += 1; base = "body" if bodies == 1 else f"body_{bodies}"
        else:
            acc.setdefault(mat, len(acc) + 1); base = f"accessory_{acc[mat]}"
        n, k = base + kind[p[0]], 2
        while n in taken:
            n = f"{base}{kind[p[0]]}_{k}"; k += 1
        taken.add(n); names[p[1]] = n + ".png"
    for i, h in enumerate(hashes):
        names.setdefault(h, f"extra_{i + 1}.png")
    return names

def stand_gear(obj, camera=None, has_gear=None):
    """Roblox exports held gear in the 'arm raised' grip but leaves the arm down, so the gear
    sits wrong (a staff lies flat). Raise the gear arm forward with the gear, like Roblox does.
    has_gear: from the avatar's public item list (True / False), None = unknown (guess by shape).
    Returns (obj bytes, new bounds or None if nothing moved)."""
    if has_gear is False:
        return obj, None
    lines = obj.decode("utf-8", "ignore").split("\n")
    V, N, vline, nline = [], [], [], []
    groups, cur = {}, ""
    for k, line in enumerate(lines):
        if line.startswith("v "):
            V.append([float(x) for x in line.split()[1:4]]); vline.append(k)
        elif line.startswith("vn "):
            N.append([float(x) for x in line.split()[1:4]]); nline.append(k)
        elif line.startswith("g ") or line.startswith("o "):
            cur = line[2:].strip()
        elif line.startswith("f "):
            g = groups.setdefault(cur, (set(), set()))
            for t in line.split()[1:]:
                q = t.split("/")
                if q[0].lstrip("-").isdigit() and int(q[0]) > 0:
                    g[0].add(int(q[0]) - 1)
                if len(q) > 2 and q[2].isdigit():
                    g[1].add(int(q[2]) - 1)
    if not V or not groups:
        return obj, None

    def box(ix):
        P = [V[i] for i in ix]
        return [min(p[a] for p in P) for a in range(3)], [max(p[a] for p in P) for a in range(3)]

    body = {g: box(v) for g, (v, _) in groups.items() if g.lower().startswith("player") and v}
    if not body:
        return obj, None
    blo = [min(b[0][a] for b in body.values()) for a in range(3)]
    bhi = [max(b[1][a] for b in body.values()) for a in range(3)]
    lat = 0 if bhi[0] - blo[0] >= bhi[2] - blo[2] else 2       # left-right axis
    dep = 2 - lat                                                # front-back axis
    # the avatar's front: the side Roblox's thumbnail camera is on (Roblox avatars face -z)
    cam = [camera[k] for k in "xyz"] if camera else None
    front = (1 if cam[dep] > (blo[dep] + bhi[dep]) / 2 else -1) if cam else -1
    mid, half = (blo[lat] + bhi[lat]) / 2, (bhi[lat] - blo[lat]) / 2
    hands = []          # (hand box, arm part names, shoulder joint)
    # gear is always in the right hand; facing -z that's +x (Roblox: right = look x up)
    right = -front if lat == 0 else front
    for side in ((right,) if has_gear else (-1, 1)):
        arm = [g for g, b in body.items() if side * ((b[0][lat] + b[1][lat]) / 2 - mid) > 0.6 * half]
        if arm:
            lo, hi = min((body[g] for g in arm), key=lambda b: b[0][1])
            top = max((body[g] for g in arm), key=lambda b: b[1][1])
            width = top[1][lat] - top[0][lat]
            # the shoulder joint sits in the middle of the arm's top block (Roblox rigs)
            shoulder = [0, top[1][1] - width / 2, (top[0][dep] + top[1][dep]) / 2]
            hands.append((lo[:], [hi[0], min(hi[1], lo[1] + 0.6), hi[2]], arm, shoulder))

    def touches(b, h, pad=0.2):
        return all(b[0][a] <= h[1][a] + pad and b[1][a] >= h[0][a] - pad for a in range(3))

    moved_v, moved_n, moved = set(), set(), False
    changed_v, changed_n = set(), set()
    for h0, h1, arm, shoulder in hands:
        h = (h0, h1)
        neck = shoulder[1] + 0.5 * (h1[lat] - h0[lat])        # above this: hats and hair, never gear
        if has_gear:
            # we know a gear is held: whatever is in the right hand is it, whatever its shape
            tool = [g for g, (v, _) in groups.items() if g not in body and v
                    and touches(box(v), h, 0.25) and box(v)[0][1] < neck]
            # plus any parts attached to those (a jar's lid, a bucket's handle...)
            grew = True
            while grew and tool:
                grew = False
                tb = [box(groups[g][0]) for g in tool]
                u = ([min(b[0][a] for b in tb) for a in range(3)], [max(b[1][a] for b in tb) for a in range(3)])
                for g, (v, _) in groups.items():
                    if g not in body and g not in tool and v and box(v)[0][1] < neck and touches(box(v), u, 0.05):
                        tool.append(g); grew = True
        else:
            # not sure: only things clearly sticking out of the hand (a long staff or sword)
            tool = [g for g, (v, _) in groups.items() if g not in body and v
                    and touches(box(v), h) and (box(v)[1][dep] > bhi[dep] + 0.6 or box(v)[0][dep] < blo[dep] - 0.6)]
            if tool:
                tb = [box(groups[g][0]) for g in tool]
                tlo = [min(b[0][a] for b in tb) for a in range(3)]; thi = [max(b[1][a] for b in tb) for a in range(3)]
                for g, (v, _) in groups.items():      # other loose parts of the same gear
                    if g in body or g in tool or not v:
                        continue
                    b = box(v)
                    out_front = b[0][dep] > bhi[dep] + 0.3 or b[1][dep] < blo[dep] - 0.3
                    in_line = b[0][1] >= tlo[1] - 0.5 and b[1][1] <= thi[1] + 0.5 and b[0][lat] >= tlo[lat] - 0.5 and b[1][lat] <= thi[lat] + 0.5
                    if out_front and in_line:
                        tool.append(g)
        if not tool:
            continue
        hc = [(h[0][a] + h[1][a]) / 2 for a in range(3)]
        fwd = front
        # raise the arm to the front and turn the gear with it, around the shoulder: Roblox's own
        # holding pose (arm out in front, gear hanging from the hand the way Roblox grips it)
        piv = shoulder
        for g in tool + arm:
            moved_v |= groups[g][0]; moved_n |= groups[g][1]
        moved = True
        changed_v |= moved_v; changed_n |= moved_n
        for i in list(moved_v):
            p = V[i]; dy, dd = p[1] - piv[1], p[dep] - piv[dep]
            q = p[:]; q[1] = piv[1] + fwd * dd; q[dep] = piv[dep] - fwd * dy
            V[i] = q
        for i in list(moved_n):
            n = N[i]; q = n[:]; q[1] = fwd * n[dep]; q[dep] = -fwd * n[1]
            N[i] = q
        # then put the gear just in front of the fist (touching it, centred on the hand),
        #    so it doesn't go through the arm
        av = set()
        for g in arm:
            av |= groups[g][0]
        tv = set()
        for g in tool:
            tv |= groups[g][0]
        tip = (max if front > 0 else min)(V[i][dep] for i in av)            # end of the hand
        near = (min if front > 0 else max)(V[i][dep] for i in tv)           # gear's side facing the body
        alo, ahi = min(V[i][lat] for i in av), max(V[i][lat] for i in av)
        tlo2, thi2 = min(V[i][lat] for i in tv), max(V[i][lat] for i in tv)
        shift_d = tip + front * 0.02 - near
        shift_l = (alo + ahi) / 2 - (tlo2 + thi2) / 2
        for i in tv:
            q = V[i][:]; q[dep] += shift_d; q[lat] += shift_l; V[i] = q
        moved_v, moved_n = set(), set()
    if not moved:
        return obj, None
    for i in changed_v:
        lines[vline[i]] = "v %.6f %.6f %.6f" % tuple(V[i])
    for i in changed_n:
        lines[nline[i]] = "vn %.6f %.6f %.6f" % tuple(N[i])
    lo = [min(p[a] for p in V) for a in range(3)]; hi = [max(p[a] for p in V) for a in range(3)]
    return "\n".join(lines).encode(), (lo, hi)


def download(meta, step, has_gear=None):
    hashes = [meta["obj"], meta["mtl"]] + list(meta["textures"])
    got, done = {}, 0
    with ThreadPoolExecutor(8) as ex:
        for h, data in zip(hashes, ex.map(fetch_hash, hashes)):
            got[h] = data; done += 1; step(done, len(hashes))
    mtl = got[meta["mtl"]].decode("utf-8", "ignore")
    names = texture_names(mtl, meta["textures"])
    for h, n in names.items():
        mtl = mtl.replace(h, f"textures/{n}")
    obj, bounds = stand_gear(got[meta["obj"]], (meta.get("camera") or {}).get("position"), has_gear)
    aabb = meta["aabb"]
    if bounds:       # gear was turned upright: frame the avatar as it is now
        aabb = {"min": dict(zip("xyz", bounds[0])), "max": dict(zip("xyz", bounds[1]))}
    files = {
        "avatar.obj": b"mtllib avatar.mtl\n" + obj,
        "avatar.mtl": mtl.encode(),
        "camera.json": json.dumps({"camera": meta["camera"], "aabb": aabb}, indent=2).encode(),
    }
    for h in meta["textures"]:
        files[f"textures/{names[h]}"] = got[h]
    return files

# ---------------------------------------------------------------- renderer (same as render_gif.py)
FILES = {}

def read_bytes(name):
    return FILES[name]

def exists(name):
    return name in FILES

def read_text(name):
    return read_bytes(name).decode("utf-8", errors="ignore").splitlines(keepends=True)

if Image is not None:
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

# ---------------------------------------------------------------- spin (one process per core)
_scene = None

def _init(files):
    global _scene
    FILES.update(files)
    _scene = Scene()

def _frame(job):
    i, eye, center, fov, size, ss = job
    return i, _scene.render(np.array(eye), np.array(center), fov, size, ss)

def spin(files, size, frames, ss, step):
    from multiprocessing import Pool
    cam = json.loads(files["camera.json"])
    lo = np.array([cam["aabb"]["min"][k] for k in "xyz"]); hi = np.array([cam["aabb"]["max"][k] for k in "xyz"])
    center = (lo + hi) / 2
    fov = cam["camera"]["fov"]
    d = np.array([cam["camera"]["direction"][k] for k in "xyz"])
    elev = math.asin(max(-0.6, min(0.6, d[1] / np.linalg.norm(d))))
    radius = np.linalg.norm(hi - lo) / 2
    dist = radius / math.sin(math.radians(fov) / 2) * 1.02
    jobs = []
    for i in range(frames):
        yaw = 2 * math.pi * i / frames
        off = np.array([math.sin(yaw) * math.cos(elev), math.sin(elev), -math.cos(yaw) * math.cos(elev)])
        jobs.append((i, tuple(center + off * dist), tuple(center), fov, size, ss))
    shots = [None] * frames
    with Pool(max(1, min(os.cpu_count() or 1, 8)), _init, (files,)) as p:
        for n, (i, img) in enumerate(p.imap_unordered(_frame, jobs), 1):
            shots[i] = img; step(n, frames)
    al = np.max([f[..., 3] for f in shots], 0); ys, xs = np.where(al > 0)
    pad = max(4, size // 60)
    y0, y1 = max(ys.min() - pad, 0), min(ys.max() + pad + 1, size)
    x0, x1 = max(xs.min() - pad, 0), min(xs.max() + pad + 1, size)
    rgba = [Image.fromarray(np.ascontiguousarray(f[y0:y1, x0:x1]), "RGBA") for f in shots]
    front = io.BytesIO(); rgba[0].save(front, "PNG")
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
    gif = io.BytesIO()
    write_gif(gif, idx, (pal.getpalette() + [0] * 768)[:765] + [0, 0, 0], 70)
    return gif.getvalue(), front.getvalue()

README = """{user} - Roblox avatar in 3D
==========================================

WHAT'S IN THIS FOLDER
  avatar_spin.gif   The avatar spinning 360 degrees, transparent background.
  avatar_front.png  The avatar from the front, transparent background.
  avatar.obj        The 3D model (body, clothes, hair, accessories). Open this one.
  avatar.mtl        Materials: tells the model which picture goes where. Keep it next to avatar.obj.
  textures/         The pictures painted on the model (body.png, accessory_N.png, ...).
  camera.json       The angle Roblox uses for its own thumbnail.

OPEN IT
  Windows:  right-click avatar.obj > Open with > 3D Viewer.
  Blender:  File > Import > Wavefront (.obj) > avatar.obj  (textures load automatically).
  Keep the files together - moving avatar.obj on its own loses the colours.
"""

# ---------------------------------------------------------------- main
def main():
    vt = _vt()
    if LIVE:
        _pics()
        threading.Thread(target=_spinner, daemon=True).start()
        import atexit
        atexit.register(show_cursor)          # the cursor always comes back, even after Ctrl+C
    ap = argparse.ArgumentParser(prog="avatar3d", description="Download a Roblox avatar as a 3D model + spinning GIF.")
    ap.add_argument("username", nargs="?")
    ap.add_argument("--size", type=int, default=400, help="GIF size in pixels (default 400)")
    ap.add_argument("--frames", type=int, default=36, help="frames per turn (default 36)")
    a = ap.parse_args()
    asked = not a.username
    if asked:
        clear(vt)

    out()
    out(f"  {C['b']}avatar3d{C['0']}")
    out()
    if np is None:
        fail("needs numpy and Pillow:  pip install numpy pillow")
        return 1
    name = a.username
    if asked:
        try:
            name = input(f"  {C['dim']}username ›{C['0']} ").strip()
        except (EOFError, KeyboardInterrupt):
            return 1
        sys.stdout.write("\x1b[1A\r\x1b[K"); sys.stdout.flush()
        if not name:
            return 1
    code = 0
    try:
        u = user_by_name(name)
        out(f"  {C['b']}{u['displayName']}{C['0']} {C['dim']}@{u['name']}{C['0']}")
        out()
        bar("3D model", 0, 1)
        meta = file_list(u["id"])
        files = download(meta, lambda d, t: bar("3D model", d, t), avatar_has_gear(u["id"]))
        ok("3D model", f"{len(files)} files · {mb(sum(len(v) for v in files.values()))}")
        bar("spinning gif", 0, a.frames)
        gif, front = spin(files, a.size, a.frames, 2, lambda d, t: bar("spinning gif", d, t))
        ok("spinning gif", f"{a.frames} frames · {mb(len(gif))}")
        path = free_name(downloads_dir(), f"{u['name']}_3d.zip")
        d = f"{u['name']}_3d/"
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr(d + "README.txt", README.format(user=u["name"]))
            z.writestr(d + "avatar_spin.gif", gif, zipfile.ZIP_STORED)
            z.writestr(d + "avatar_front.png", front, zipfile.ZIP_STORED)
            for n, v in files.items():
                z.writestr(d + n, v, zipfile.ZIP_STORED if n.endswith(".png") else zipfile.ZIP_DEFLATED)
        ok("saved", path)
        out()
    except KeyboardInterrupt:
        fail("stopped"); code = 1
    except Exception as e:
        fail(str(e)); code = 1
    show_cursor()
    if asked:
        try:
            input(f"  {C['dim']}press enter to close{C['0']}")
        except (EOFError, KeyboardInterrupt):
            pass
    return code


if __name__ == "__main__":
    sys.exit(main())
