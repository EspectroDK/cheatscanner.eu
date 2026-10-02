"""Map mesh checks for the server (cs2-analyzer maps-check, admin page) and picking up pushed meshes."""

import json
import os
from datetime import datetime, timezone
from types import SimpleNamespace

import numpy as np

from cs2_analyzer.cli import main
from cs2_analyzer.evidence.clips import render_geometry
from cs2_analyzer.geometry.mesh import box_triangles
from cs2_analyzer.geometry.status import check_mesh, map_status
from cs2_analyzer.storage import models as M
from cs2_analyzer.storage.repository import Database
from cs2_analyzer.storage.stats import recent_demo_patches


def _mesh(path, n=2000, patch=None, seed=0):
    rng = np.random.default_rng(seed)
    base = rng.uniform(-2000, 2000, size=(n, 1, 3))
    tris = (base + rng.uniform(0, 50, size=(n, 3, 3))).astype("<f4")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(tris.tobytes())
    if patch is not None:
        path.with_name(path.name + ".json").write_text(json.dumps({"patch_version": patch}))
    return path


def test_check_mesh_accepts_a_real_mesh_and_rejects_broken_ones(tmp_path):
    n, problem = check_mesh(_mesh(tmp_path / "de_mirage.tri", patch=14185))
    assert problem is None and n > 1000
    (tmp_path / "empty.tri").write_bytes(b"")
    assert "triangles" in check_mesh(tmp_path / "empty.tri")[1]
    bad = _mesh(tmp_path / "de_bad.tri")
    bad.with_name("de_bad.tri.json").write_text("{not json")
    assert "patch_version" in check_mesh(bad)[1]


def test_maps_check_command(tmp_path, capsys):
    _mesh(tmp_path / "in" / "de_inferno.tri", patch=14185)
    assert main(["maps-check", str(tmp_path / "in")]) == 0
    assert "ok" in capsys.readouterr().out
    (tmp_path / "in" / "render").mkdir()
    (tmp_path / "in" / "render" / "de_inferno.tri").write_bytes(b"\x00" * 36)
    assert main(["maps-check", str(tmp_path / "in")]) == 1
    assert "BAD" in capsys.readouterr().out


def test_map_status_flags_stale_and_missing_meshes(tmp_path):
    maps = tmp_path / "maps"
    _mesh(maps / "de_mirage.tri", patch=14100)
    _mesh(maps / "nuke.tri", patch=14180)
    _mesh(maps / "render" / "de_mirage.tri")
    rows = {r["map"]: r for r in map_status(maps, maps / "render",
                                             {"de_mirage": 14185, "de_nuke": 14185, "de_train": 14185}, 10)}
    assert rows["de_mirage"]["stale"] and rows["de_mirage"]["renderMesh"]
    assert not rows["de_nuke"]["stale"] and not rows["de_nuke"]["renderMesh"]
    assert rows["de_train"]["missing"] and not rows["de_train"]["stale"]


def test_recent_demo_patches(tmp_path):
    db = Database(f"sqlite:///{tmp_path}/p.sqlite")
    db.init_schema()
    now = datetime.now(timezone.utc)
    with db.session() as s:
        for i, (m, p) in enumerate([("de_mirage", "14180"), ("de_mirage", "14185"), ("de_nuke", None)]):
            s.add(M.Match(match_id=f"m{i}", demo_sha256=f"h{i}", map=m, processing_status="COMPLETED",
                          processed_at=now, meta={"patch_version": p}))
    assert recent_demo_patches(db, datetime(2020, 1, 1, tzinfo=timezone.utc)) == {"de_mirage": 14185}


def test_clips_pick_up_a_pushed_render_mesh_without_restart(tmp_path):
    folder = tmp_path / "render"
    folder.mkdir()
    f = folder / "de_test.tri"
    f.write_bytes(box_triangles((0, 0, 0), (10, 10, 10)).astype("<f4").tobytes())
    result = SimpleNamespace(geometry=SimpleNamespace(map_name="de_test"))
    first = render_geometry(result, {"render_maps_dir": str(folder)})
    assert len(first.triangles) == 12
    f.write_bytes(np.concatenate([box_triangles((0, 0, 0), (10, 10, 10)),
                                  box_triangles((20, 0, 0), (30, 10, 10))]).astype("<f4").tobytes())
    st = f.stat()
    os.utime(f, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000_000))
    assert len(render_geometry(result, {"render_maps_dir": str(folder)}).triangles) == 24
