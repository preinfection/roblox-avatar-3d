"""Builds cli/avatar3d.py: cli/template.py + the renderer and GIF writer from extension/render_gif.py
(one source for both, so the program and the zip's render_gif.py always draw the same)."""
import os
here = os.path.dirname(os.path.abspath(__file__))
src = open(os.path.join(here, "..", "extension", "render_gif.py"), encoding="utf-8").read()
tpl = open(os.path.join(here, "template.py"), encoding="utf-8").read()
a = src.index("# ---------------------------------------------------------------- loading")
b = src.index("# ---------------------------------------------------------------- main")
c = src.index("def _lzw(")
d = src.index('if __name__ == "__main__":')
renderer = src[a:b].rstrip() + "\n\n\n" + src[src.rindex("\n\n\n", 0, c) + 3:d].rstrip() + "\n"
renderer = renderer.replace("class _Keep:", "class _Keep:", 1)
out = tpl.replace("# --RENDERER--\n", renderer)
assert "# --RENDERER--" not in out
open(os.path.join(here, "avatar3d.py"), "w", encoding="utf-8", newline="\n").write(out)
print("built cli/avatar3d.py", len(out.splitlines()), "lines")
