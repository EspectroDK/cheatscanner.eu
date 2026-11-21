"""Deletes old evidence clips so the server disk doesn't fill up (``cs2-analyzer prune-clips``, run nightly).

Only the videos and their poster images go: the events, their explanations and charts stay, and the site says
the clip was removed. Age is the clip file's own age, so a re-analyzed match gets a fresh clip that is kept again.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path


@dataclass
class PruneResult:
    files: int = 0
    bytes: int = 0


def prune_clips(output_dir: str | Path, max_age_days: float, *, dry_run: bool = False,
                now: float | None = None) -> PruneResult:
    """Delete clip videos (``<output>/<match>/<steam id>/clips/*.mp4``) older than ``max_age_days``, with their
    ``.jpg`` posters. ``max_age_days <= 0`` deletes nothing."""
    res = PruneResult()
    root = Path(output_dir)
    if max_age_days <= 0 or not root.is_dir():
        return res
    cutoff = (now if now is not None else time.time()) - max_age_days * 86400
    for video in root.glob("*/*/clips/*.mp4"):
        try:
            st = video.stat()
        except OSError:
            continue
        if st.st_mtime >= cutoff:
            continue
        for f in (video, video.with_suffix(".jpg")):
            try:
                size = f.stat().st_size
            except OSError:
                continue
            if not dry_run:
                try:
                    f.unlink()
                except OSError:
                    continue
            res.files += 1
            res.bytes += size
        if not dry_run:
            try:
                video.parent.rmdir()  # only when it is empty now
            except OSError:
                pass
    return res
