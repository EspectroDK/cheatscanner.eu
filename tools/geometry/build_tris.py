"""Build ``data/maps/<map>.tri`` collision meshes from a local CS2 install.

The mesh then matches the installed game patch exactly. Per map:

1. Source2Viewer-CLI (ValveResourceFormat, https://github.com/ValveResourceFormat/ValveResourceFormat/releases)
   prints the PHYS block of ``maps/<map>/world_physics.vmdl_c`` from ``game/csgo/maps/<map>.vpk`` as KV3 text.
2. awpy's ``VphysParser`` (``pip install awpy``, >= 2.0) keeps the collision attributes of the "default" group,
   i.e. world geometry and bullet-blocking props. It drops ``passbullets`` (glass, grates), player/NPC clips,
   grenade clips and sky, and writes float32 triangles (9 floats each), the format ``geometry/mesh.py`` reads.

awpy is a tool dependency only, so install it in its own environment:

    python -m venv .awpy && .awpy/Scripts/pip install awpy
    .awpy/Scripts/python tools/geometry/build_tris.py --s2v path/to/Source2Viewer-CLI.exe
    .awpy/Scripts/python tools/geometry/build_tris.py --s2v ... de_ancient de_anubis

With no map names, the Active Duty group (``mg_active`` in the game's gamemodes.txt) is built.
"""

from __future__ import annotations

import argparse
import pathlib
import re
import subprocess
import sys
import tempfile
import time

DEFAULT_GAME = pathlib.Path(r"C:\Program Files (x86)\Steam\steamapps\common\Counter-Strike Global Offensive\game\csgo")


def s2v(exe: str, *args: str) -> str:
    r = subprocess.run([exe, *args], capture_output=True, text=True, encoding="utf-8", errors="replace")
    return r.stdout


def active_duty(exe: str, game: pathlib.Path) -> list[str]:
    with tempfile.TemporaryDirectory() as tmp:
        out = pathlib.Path(tmp) / "gamemodes.txt"
        s2v(exe, "-i", str(game / "pak01_dir.vpk"), "-f", "gamemodes.txt", "-o", str(out))
        text = out.read_text(encoding="utf-8", errors="replace")
    block = re.search(r'"name"\s+"mg_active".*?"maps"\s*\{(.*?)\}', text, re.S)
    if not block:
        raise SystemExit("mg_active not found in gamemodes.txt; pass map names explicitly")
    return re.findall(r'"(\w+)"', block.group(1))


def game_patch(game: pathlib.Path) -> tuple[int | None, str | None]:
    """(demo-style patch number, version string) from steam.inf: PatchVersion=1.41.8.5 -> 14185."""
    try:
        text = (game / "steam.inf").read_text(errors="replace")
    except OSError:
        return None, None
    m = re.search(r"PatchVersion=([\d.]+)", text)
    if not m:
        return None, None
    return int(m.group(1).replace(".", "")), m.group(1)


def build(exe: str, game: pathlib.Path, out_dir: pathlib.Path, map_name: str) -> int:
    import datetime
    import json

    from awpy.visibility import VphysParser

    text = s2v(exe, "-i", str(game / "maps" / f"{map_name}.vpk"), "-f", f"maps/{map_name}/world_physics.vmdl_c",
               "-b", "PHYS")
    i = text.find("<!-- kv3")
    if i < 0:
        raise RuntimeError("no PHYS block in world_physics.vmdl_c")
    with tempfile.TemporaryDirectory() as tmp:
        vphys = pathlib.Path(tmp) / f"{map_name}.vphys"
        vphys.write_text(text[i:], encoding="utf-8")
        parser = VphysParser(vphys)
        out_dir.mkdir(parents=True, exist_ok=True)
        parser.to_tri(out_dir / f"{map_name}.tri")
    patch, version = game_patch(game)
    (out_dir / f"{map_name}.tri.json").write_text(json.dumps({
        "map": map_name, "patch_version": patch, "game_version": version, "triangles": len(parser.triangles),
        "source": f"{game / 'maps' / (map_name + '.vpk')}: maps/{map_name}/world_physics.vmdl_c",
        "built": datetime.date.today().isoformat(), "removed_by_audit": 0,
    }, indent=2))
    return len(parser.triangles)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("maps", nargs="*", help="map names (default: the game's Active Duty group)")
    ap.add_argument("--s2v", required=True, help="path to Source2Viewer-CLI")
    ap.add_argument("--game", default=str(DEFAULT_GAME), help="the game/csgo directory")
    ap.add_argument("--out", default="data/maps")
    args = ap.parse_args(argv)
    game = pathlib.Path(args.game)
    maps = args.maps or active_duty(args.s2v, game)
    failed = 0
    for m in maps:
        t0 = time.time()
        try:
            n = build(args.s2v, game, pathlib.Path(args.out), m)
            print(f"{m}: {n} triangles ({time.time() - t0:.0f} s)")
        except Exception as e:  # keep going with the other maps
            failed += 1
            print(f"FAIL {m}: {type(e).__name__}: {e}", file=sys.stderr)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
