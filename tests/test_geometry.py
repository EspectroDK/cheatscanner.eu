import numpy as np
import pytest

from cs2_analyzer.geometry.angles import (
    aim_error_components,
    angle_between_vectors,
    angular_distance,
    angular_speed,
    bearing,
    derivatives,
    pearson,
    sign_changes,
    unwrap_yaw,
    view_vector,
    wrap180,
    yaw_delta,
)
from cs2_analyzer.geometry.mesh import MapGeometry, NumpyRaycaster, box_triangles, drop_degenerate, load_tri


def test_wrap_and_yaw_delta():
    assert wrap180(190) == pytest.approx(-170)
    assert wrap180(-180) == pytest.approx(-180)
    assert wrap180(540) == pytest.approx(-180)
    assert yaw_delta(179, -179) == pytest.approx(-2)
    assert yaw_delta(-179, 179) == pytest.approx(2)


def test_view_vector_source_conventions():
    np.testing.assert_allclose(view_vector(0, 0), [1, 0, 0], atol=1e-12)
    np.testing.assert_allclose(view_vector(0, 90), [0, 1, 0], atol=1e-12)
    # positive pitch looks DOWN in Source
    assert view_vector(45, 0)[2] < 0
    np.testing.assert_allclose(np.linalg.norm(view_vector(33, -120)), 1.0)


def test_bearing_roundtrip():
    src = np.array([10.0, 20.0, 30.0])
    for pitch, yaw in [(0, 0), (10, 45), (-30, 170), (60, -100)]:
        dst = src + view_vector(pitch, yaw) * 500
        y, p = bearing(src, dst)
        assert y == pytest.approx(yaw, abs=1e-9)
        assert p == pytest.approx(pitch, abs=1e-9)


def test_angular_distance_matches_vector_angle_and_is_accurate_small():
    rng = np.random.default_rng(1)
    p1, y1 = rng.uniform(-80, 80, 200), rng.uniform(-180, 180, 200)
    p2, y2 = rng.uniform(-80, 80, 200), rng.uniform(-180, 180, 200)
    a = angular_distance(p1, y1, p2, y2)
    b = angle_between_vectors(view_vector(p1, y1), view_vector(p2, y2))
    np.testing.assert_allclose(a, b, atol=1e-8)
    assert angular_distance(0, 0, 0, 0.001) == pytest.approx(0.001, rel=1e-6)
    assert angular_distance(0, 179.5, 0, -179.5) == pytest.approx(1.0)


def test_aim_error_components_signs():
    # target to the left (higher yaw) and below (higher pitch)
    h, v, tot = aim_error_components(0.0, 0.0, 2.0, 3.0)
    assert h > 0 and v > 0
    assert tot == pytest.approx(np.hypot(3.0, 2.0), rel=0.01)


def test_unwrap_and_speed():
    yaw = np.array([170, 175, -178, -170])
    np.testing.assert_allclose(unwrap_yaw(yaw), [170, 175, 182, 190])
    s = angular_speed(np.zeros(4), yaw, 1 / 64)
    assert np.isnan(s[0])
    np.testing.assert_allclose(s[1:], [5 * 64, 7 * 64, 8 * 64], rtol=1e-6)


def test_derivatives_of_polynomial():
    dt = 1 / 64
    t = np.arange(200) * dt
    x = 3 * t ** 3
    v, a, j = derivatives(x, dt)
    mid = slice(10, -10)
    np.testing.assert_allclose(v[mid], 9 * t[mid] ** 2, rtol=0.01)
    np.testing.assert_allclose(a[mid], 18 * t[mid], rtol=0.02)
    np.testing.assert_allclose(j[mid], 18, rtol=0.05)


def test_pearson_and_sign_changes():
    x = np.linspace(0, 10, 100)
    assert pearson(x, 2 * x + 1) == pytest.approx(1.0)
    assert pearson(x, -x) == pytest.approx(-1.0)
    assert np.isnan(pearson(x, np.ones(100)))
    s = np.array([1, 2, 0, -1, -2, 0.01, 3])
    np.testing.assert_array_equal(sign_changes(s, min_abs=0.1), [3, 6])


def test_raycast_wall_blocks_and_numpy_matches_embree():
    tris = box_triangles((400, -500, -50), (420, 500, 300))
    geo = MapGeometry("t", tris, backend="numpy")
    a = np.array([[0, 0, 64.0], [0, 0, 64.0], [0, 0, 64.0]])
    b = np.array([[800, 0, 64.0], [800, 1500, 64.0], [300, 0, 64.0]])
    np.testing.assert_array_equal(geo.segment_clear(a, b), [False, True, True])
    pytest.importorskip("embreex")
    emb = MapGeometry("t", tris, backend="embree")
    rng = np.random.default_rng(3)
    a = rng.uniform(-1000, 1000, (500, 3))
    b = rng.uniform(-1000, 1000, (500, 3))
    np.testing.assert_array_equal(geo.segment_clear(a, b), emb.segment_clear(a, b))


def test_segment_end_tolerance_ignores_touching_surfaces():
    floor = box_triangles((-1000, -1000, -10), (1000, 1000, 0))
    geo = MapGeometry("t", floor, backend="numpy")
    # target point resting on the floor must not count as blocked
    assert geo.segment_clear(np.array([[0, 0, 64.0]]), np.array([[500, 0, 0.5]]))[0]


def test_load_tri_hex_and_binary(tmp_path):
    tris = box_triangles((0, 0, 0), (1, 2, 3))
    raw = tris.astype("<f4").tobytes()
    (tmp_path / "a.tri").write_bytes(raw)
    (tmp_path / "b.tri").write_text(" ".join(f"{b:02x}" for b in raw))
    np.testing.assert_allclose(load_tri(tmp_path / "a.tri"), tris)
    np.testing.assert_allclose(load_tri(tmp_path / "b.tri"), tris)
    degenerate = np.zeros((1, 3, 3), np.float32)
    assert len(drop_degenerate(np.concatenate([tris, degenerate]))) == len(tris)
