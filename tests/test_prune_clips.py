import os
import time

from cs2_analyzer.evidence.prune import prune_clips


def _clip(root, match, name, age_days, now):
    d = root / match / "76561197960287930" / "clips"
    d.mkdir(parents=True, exist_ok=True)
    t = now - age_days * 86400
    for suffix, size in ((".mp4", 1000), (".jpg", 100)):
        f = d / f"{name}{suffix}"
        f.write_bytes(b"x" * size)
        os.utime(f, (t, t))
    return d / f"{name}.mp4"


def test_prune_clips_deletes_only_old_videos_and_posters(tmp_path):
    now = time.time()
    old = _clip(tmp_path, "m1", "old", 40, now)
    new = _clip(tmp_path, "m2", "new", 5, now)
    plot = tmp_path / "m1" / "76561197960287930" / "plots" / "old.png"
    plot.parent.mkdir(parents=True)
    plot.write_bytes(b"p")
    os.utime(plot, (now - 90 * 86400,) * 2)

    dry = prune_clips(tmp_path, 30, dry_run=True, now=now)
    assert (dry.files, dry.bytes) == (2, 1100) and old.exists()

    res = prune_clips(tmp_path, 30, now=now)
    assert (res.files, res.bytes) == (2, 1100)
    assert not old.exists() and not old.with_suffix(".jpg").exists() and not old.parent.exists()
    assert new.exists() and new.with_suffix(".jpg").exists() and plot.exists()


def test_prune_clips_off(tmp_path):
    now = time.time()
    old = _clip(tmp_path, "m1", "old", 400, now)
    assert prune_clips(tmp_path, 0, now=now).files == 0 and old.exists()
