<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="assets/wordmark-dark.svg">
    <img src="assets/wordmark-light.svg" alt="preinfection" width="320">
  </picture>
</p>

<h3 align="center">avatar 3d</h3>
<p align="center">Any Roblox avatar as a 3D model and a 360° spinning GIF, in one zip.</p>

---

## Command prompt (no Roblox login)

```
pip install numpy pillow
python cli/avatar3d.py
```

It clears the window and asks for a username. Or give it straight away:
`python cli/avatar3d.py builderman`. Bigger, smoother GIF: `--size 720 --frames 48`.

The zip is saved where your downloads go (your browser's download folder, or Windows'
Downloads folder) and the full path is shown at the end. In Windows Terminal the progress
icon is the Roblox logo, turning while it works.

## Browser extension (uses your Roblox login)

`brave://extensions` or `chrome://extensions` → **Developer mode** → **Load unpacked** → pick the
`extension` folder. Log in to roblox.com, open a profile (or type a username) and click
**Download 3D**. The GIF is made right in the browser in about two seconds.

## What's in the zip

```
<username>_3d/
  avatar_spin.gif   the avatar spinning 360°, transparent background
  avatar_front.png  the avatar from the front, transparent background
  avatar.obj        the 3D model - open this
  avatar.mtl        materials (which picture goes where)
  textures/         body.png, accessory_N.png (+ _normal, _roughness, _glow)
  camera.json       Roblox's thumbnail camera
  README.txt        what each file is and how to open it
```

The extension's zip also has `render_gif.py`, to make the GIF again at other sizes.

Windows: open `avatar.obj` with 3D Viewer. Blender: File › Import › Wavefront (.obj). Keep the
files together, or the colours are lost.

Held gear is shown the way Roblox shows it in game: the arm raised in front, the gear in the hand
(Roblox's export leaves the arm down, which puts the gear in the wrong place).

## How it works

- Roblox hands out the 3D model (`thumbnails.roblox.com/v1/users/avatar-3d`) only to logged-in
  users. The **extension** asks with your own browser session, the same request roblox.com's own
  3D viewer makes. It never reads or stores your cookies and sends nothing anywhere except Roblox.
  Permissions: `downloads`, `scripting`, and `roblox.com` / `rbxcdn.com` only.
- The **command prompt** program gets the same file list from `mathium.xyz/api/avatar3d`, which
  asks Roblox with one throwaway account. That service can only fetch an avatar's 3D file list,
  keeps no logs and is rate limited.
- The model, material and texture files are public on Roblox's CDN (`t0`–`t7.rbxcdn.com`); both
  download them, fix held gear, render the GIF and zip it all on your computer.

Accessories and clothing belong to their creators. This is for viewing avatars and making renders,
not for re-uploading other people's items.

## Development

- `cli/avatar3d.py` is built from `cli/template.py` + the renderer in `extension/render_gif.py`:
  run `python cli/build.py` after changing either.
- `npm test -- <saved avatar-3d json> <out.zip> <username> [userId]` runs the extension's download, gear fix,
  GIF render and zip end to end in Node.
