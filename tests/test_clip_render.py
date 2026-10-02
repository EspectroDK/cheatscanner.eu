"""Renders a real (small) evidence clip from a synthetic scenario, including its thumbnail frame."""

from types import SimpleNamespace

from PIL import Image

from cs2_analyzer.evidence.clips import poster_path, render_clip
from cs2_analyzer.pipeline import build_analysis
from cs2_analyzer.testing.synthetic import SynthPlayer, aim_at, static, wall_scenario, weave

OBS, ENEMY = 1001, 2001


def test_clip_has_a_poster_frame_at_the_flagged_moment(config, tmp_path):
    sc = wall_scenario(30)
    T = sc.T
    enemy = weave(T, sc.tickrate, 800, 0, 250, 3.0, start=sc.s(16.0))
    observer = static(T, (0, 0, 0))
    eye, head = observer.copy(), enemy.copy()
    eye[:, 2] += 64
    head[:, 2] += 64
    pitch, yaw = aim_at(eye, head, lag_ticks=10, noise_deg=0.3)
    sc.add(SynthPlayer(OBS, 3, "observer", observer, pitch, yaw))
    sc.add(SynthPlayer(ENEMY, 2, "enemy", enemy, pitch * 0, yaw * 0 + 180))
    world, geometry, smoke, vis, knowledge, _, _, events = build_analysis(
        sc.to_demo(), config, detector_names=["hidden_tracking"], geometry=sc.geometry())
    ev = max((e for e in events if e.steam_id == OBS), key=lambda e: e.severity)
    result = SimpleNamespace(world=world, geometry=geometry, smoke=smoke, vis=vis, knowledge=knowledge)

    clip = render_clip(result, ev, tmp_path / "clip.mp4",
                       {"clip_seconds_before": 0.5, "clip_seconds_after": 0.25, "clip_fps": 8,
                        "clip_width": 320, "clip_height": 180})
    assert clip.stat().st_size > 1000
    with Image.open(poster_path(clip)) as im:
        assert im.size == (320, 180)


def test_player_figures_are_hidden_behind_walls():
    import numpy as np

    from cs2_analyzer.evidence.render import render_pov
    from cs2_analyzer.geometry.mesh import MapGeometry, box_triangles

    floor = box_triangles([-500, -500, -10], [1500, 500, 0])
    wall = box_triangles([400, -40, 0], [420, 40, 200])            # hides whoever stands right behind it
    geo = MapGeometry("de_test", np.concatenate([floor, wall]))
    eye = [0, 0, 64]
    visible = {"feet": [300, 120, 0], "eye": [300, 120, 64], "team": 2}
    hidden = {"feet": [600, 0, 0], "eye": [600, 0, 64], "team": 3}
    empty = render_pov(geo, eye, 0.0, 0.0, 160, 90)
    changed = lambda img: (np.abs(img - empty).sum(axis=-1) > 0.05).sum()  # noqa: E731
    assert changed(render_pov(geo, eye, 0.0, 0.0, 160, 90, players=[visible])) > 10
    assert changed(render_pov(geo, eye, 0.0, 0.0, 160, 90, players=[hidden])) == 0


def test_render_mesh_from_the_render_folder_is_preferred(tmp_path):
    from cs2_analyzer.evidence.clips import flash_alpha, render_geometry
    from cs2_analyzer.geometry.mesh import MapGeometry, box_triangles

    box_triangles([0, 0, 0], [10, 10, 10]).astype("<f4").tofile(tmp_path / "de_test.tri")
    analysis = MapGeometry("de_test", box_triangles([0, 0, 0], [5, 5, 5]))
    result = SimpleNamespace(geometry=analysis)
    assert render_geometry(result, {"render_maps_dir": str(tmp_path)}).source.endswith("de_test.tri")
    assert render_geometry(result, {"render_maps_dir": str(tmp_path / "none")}) is analysis
    assert flash_alpha(3.0) == 0.9 and flash_alpha(0.5) == 0.45 and flash_alpha(0) == 0
