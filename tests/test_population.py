import pandas as pd

from cs2_analyzer.scoring import population as pop


def frames_for(players: dict[int, float], n=6):
    """Observations where every metric for player p equals value v."""
    out = {}
    for det, col, _sign, _q in pop.METRICS:
        out[det] = pd.DataFrame([{"steam_id": p, col: v} for p, v in players.items() for _ in range(n)])
    return out


def test_percentiles_are_oriented_and_need_enough_data(tmp_path):
    ref = {"min_obs": 5, "metrics": {f"{d}.{c}": {"sign": s, "values": [float(x) for x in range(1, 101)]}
                                     for d, c, s, _ in pop.METRICS}}
    res = pop.percentiles(frames_for({1: 50.5, 2: 1000.0}), ref)
    for k, m in res[2]["metrics"].items():
        sign = ref["metrics"][k]["sign"]
        assert m["percentile"] == (1.0 if sign > 0 else 0.0)
    assert abs(res[1]["combined_percentile"] - 0.5) < 1e-9
    assert res[1]["metrics_used"] == len(pop.METRICS)
    # too few observations per metric -> no combined percentile
    few = pop.percentiles(frames_for({3: 10.0}, n=2), ref)
    assert few == {} or few[3]["combined_percentile"] is None


def test_bundled_reference_loads():
    ref = pop.load_reference()
    assert ref is not None and ref["matches"] > 41
    assert set(ref["metrics"]) == {f"{d}.{c}" for d, c, *_ in pop.METRICS}
    assert all(v["values"] == sorted(v["values"]) for v in ref["metrics"].values())


def test_build_reference_roundtrip(tmp_path):
    for m, base in [("m1", 0), ("m2", 10)]:
        d = tmp_path / m
        d.mkdir()
        for det, df in frames_for({base + 1: base + 1.0, base + 2: base + 2.0}).items():
            df.to_parquet(d / f"{det}.parquet")
    ref = pop.build_reference(tmp_path)
    assert ref["matches"] == 2
    assert ref["metrics"]["recoil.residual_ratio"]["values"] == [1.0, 2.0, 11.0, 12.0]


def test_reference_per_map_with_pooled_fallback():
    pooled = pop.load_reference()
    assert pooled is not None and pooled.get("map") is None
    assert pop.load_reference(map_name="de_nonexistent") == pooled
    mirage = pop.load_reference(map_name="de_mirage")
    assert mirage is not None and mirage["map"] == "de_mirage"
