"""Which evidence events get clips (evidence.clip_all_events / max_clips_per_match)."""

from types import SimpleNamespace

from cs2_analyzer.evidence import writer


class Ev(SimpleNamespace):
    def to_dict(self):
        return {"id": self.id}


def _result(tmp_path):
    players = {1: "NORMAL", 2: "HIGH", 3: "NORMAL"}
    events = [Ev(id=f"{sid}-{i}", steam_id=sid, confidence=c, detector_type="d", detector_version="v",
                 video_path=None, debug_plot_path=None)
              for sid, cs in {1: [0.9, 0.2], 2: [0.5], 3: []}.items() for i, c in enumerate(cs)]
    ass = {sid: SimpleNamespace(classification=c, to_dict=dict, model_version="m") for sid, c in players.items()}
    return SimpleNamespace(
        assessments=ass, events=events, output_dir=tmp_path,
        world=SimpleNamespace(index_of={1: 0, 2: 1, 3: 2}, names=["a", "b", "c"]),
        geometry=SimpleNamespace(available=True), fingerprints={}, behavior_shifts={},
        meta=SimpleNamespace(match_id="m", map_name="de_mirage", parser_name="p", parser_version="1"))


def _run(tmp_path, monkeypatch, config, **evidence):
    clipped = []
    monkeypatch.setattr("cs2_analyzer.evidence.plots.plot_event", lambda r, ev, path: path)
    monkeypatch.setattr("cs2_analyzer.evidence.clips.render_clip", lambda r, ev, path, cfg: clipped.append(ev.id) or path)
    monkeypatch.setattr(writer, "write_report", lambda result, rows: None)
    cfg = config.with_overrides({"evidence": evidence})
    writer.write_evidence(_result(tmp_path), cfg, debug=False, generate_evidence=True, say=lambda m: None)
    return clipped


def test_events_of_normal_players_get_clips_strongest_first(tmp_path, monkeypatch, config):
    assert _run(tmp_path, monkeypatch, config) == ["1-0", "2-0", "1-1"]
    assert _run(tmp_path, monkeypatch, config, max_clips_per_match=2) == ["1-0", "2-0"]


def test_only_flagged_players_when_clip_all_is_off(tmp_path, monkeypatch, config):
    assert _run(tmp_path, monkeypatch, config, clip_all_events=False) == ["2-0"]
