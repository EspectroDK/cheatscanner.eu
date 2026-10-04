"""Giving freed memory back to the operating system.

An analysis allocates and frees several large tables. glibc keeps freed heap
memory for reuse instead of returning it, so without this a worker process
keeps the high-water mark of its largest demo for as long as it runs.
"""

from __future__ import annotations

import ctypes
import gc
import sys

_libc = None
if sys.platform.startswith("linux"):
    try:
        _libc = ctypes.CDLL("libc.so.6")
        _libc.malloc_trim  # noqa: B018 (glibc only; musl has none)
    except (OSError, AttributeError):
        _libc = None


def release_memory() -> None:
    """Collect garbage and return free heap memory to the OS (glibc ``malloc_trim``)."""
    gc.collect()
    if _libc is not None:
        _libc.malloc_trim(0)
