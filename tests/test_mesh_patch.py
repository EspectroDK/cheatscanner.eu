import json

import numpy as np

from cs2_analyzer.geometry.mesh import MapGeometry, box_triangles


def test_mesh_patch_from_sidecar(tmp_path):
    tris = box_triangles([0, 0, 0], [10, 10, 10]).astype("<f4")
    tris.tofile(tmp_path / "de_test.tri")
    geo = MapGeometry.load("de_test", tmp_path)
    assert geo.available and geo.patch_version is None
    (tmp_path / "de_test.tri.json").write_text(json.dumps({"patch_version": 14185}))
    assert MapGeometry.load("de_test", tmp_path).patch_version == 14185


def test_mesh_without_sidecar_still_blocks():
    geo = MapGeometry("de_test", box_triangles([0, 0, 0], [10, 10, 10]))
    clear = geo.segment_clear(np.array([[-20.0, 5, 5], [-20.0, 50, 5]]), np.array([[30.0, 5, 5], [30.0, 50, 5]]))
    assert clear.tolist() == [False, True]
