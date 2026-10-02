"""Map screenshots for the website's map banners.

The images are in-game screenshots from https://github.com/neustcs/cs2mapsthumbnails. That repository states
no licence and the game content belongs to Valve, so they are not part of this repository: the server
downloads them (``cs2-analyzer map-images``, run by deploy/server-deploy.sh) into ``<maps_dir>/images``, and
the API serves them at ``/maps/<map>.jpg``. Without them the site shows its drawn banners.
"""

from __future__ import annotations

import io
import urllib.request
from pathlib import Path

# Pinned to the commit of 2024-04-25, so the pictures can't change under us.
SOURCE_COMMIT = "d8a8bbf0fb6ef5fa3c193d854f75dac253331d31"
SOURCE_URL = "https://raw.githubusercontent.com/neustcs/cs2mapsthumbnails/{commit}/{map}/{file}"

# One picture per map (Train has none there; it keeps the drawn banner).
SOURCE_FILES = {
    "de_mirage": "de_mirage_a_site.jpg",
    "de_dust2": "de_dust2_a_long.jpg",
    "de_inferno": "de_inferno_a_site.jpg",
    "de_nuke": "de_nuke_outside.jpg",
    "de_ancient": "de_ancient_site_a.jpg",
    "de_anubis": "de_anubis_a_site.jpg",
    "de_vertigo": "de_vertigo_a_site.jpg",
    "de_overpass": "de_overpass_a_site.jpg",
}

SIZE = (960, 540)


def images_dir(maps_dir: str | Path) -> Path:
    return Path(maps_dir) / "images"


def _download(url: str) -> bytes:
    with urllib.request.urlopen(url, timeout=60) as r:
        return r.read()


def shrink(data: bytes) -> bytes:
    """Scale a screenshot down to the banner size (1920x1080 originals are ~500 KB each)."""
    from PIL import Image

    with Image.open(io.BytesIO(data)) as im:
        im = im.convert("RGB")
        im.thumbnail(SIZE)
        out = io.BytesIO()
        im.save(out, "JPEG", quality=82, optimize=True, progressive=True)
        return out.getvalue()


def fetch(dest: Path, force: bool = False, download=_download, log=print) -> int:
    """Download the missing map pictures into ``dest``. Returns how many failed."""
    dest.mkdir(parents=True, exist_ok=True)
    failed = 0
    for map_name, file in SOURCE_FILES.items():
        target = dest / f"{map_name}.jpg"
        if target.is_file() and not force:
            continue
        url = SOURCE_URL.format(commit=SOURCE_COMMIT, map=map_name, file=file)
        try:
            data = shrink(download(url))
        except Exception as e:  # network or a broken file: keep the drawn banner for this map
            log(f"{map_name}: not downloaded ({e})")
            failed += 1
            continue
        tmp = target.with_suffix(".tmp")
        tmp.write_bytes(data)
        tmp.replace(target)
        log(f"{map_name}: saved {target} ({len(data) >> 10} KB)")
    return failed
