"""Values PostgreSQL refuses but SQLite takes: NUL characters and NaN in JSON, NUL in names.

A fetched Valve matchmaking demo failed on the server with DataError ("unsupported Unicode escape sequence")
because its header carried NUL padding into matches.metadata (JSONB). Set CS2A_TEST_PG_URL to also run
the round trip against a real PostgreSQL.
"""

import math
import os
from types import SimpleNamespace

import numpy as np
import pytest

from cs2_analyzer.storage import models as M
from cs2_analyzer.storage.models import clean_json
from cs2_analyzer.storage.repository import Database


def test_clean_json():
    out = clean_json({"a\x00": "Valve\x00\x00", "n": float("nan"), "i": float("inf"), "np": np.float32(1.5),
                      "k": np.int64(3), "b": np.bool_(True), "l": (1, float("-inf")), "arr": np.array([1, 2])})
    assert out == {"a": "Valve", "n": None, "i": None, "np": 1.5, "k": 3, "b": True, "l": [1, None], "arr": [1, 2]}
    assert not any(isinstance(v, float) and not math.isfinite(v) for v in out.values() if v is not None)


def _meta(tag):
    return SimpleNamespace(match_id=f"m-{tag}", demo_sha256=f"h-{tag}", source="x", map_name="de_mirage", mode="premier",
                           parser_name="p", parser_version="1", tickrate=64.0, server_name="Valve\x00", patch_version="1",
                           mode_source="s", extra={"header": {"server_name": "Valve CS2\x00\x00"},
                                                   "eye_position_check": {"ratio": float("nan")}})


def _round_trip(db: Database, tag: str):
    db.begin_match(_meta(tag), force=True, detector_version="d", scoring_version="s")
    db.mark_failed(f"m-{tag}", "boom \x00 in error")
    with db.session() as s:
        s.add(M.Player(steam_id=76561190000000000 + len(tag), last_known_name="name\x00" + "x" * 300))
    with db.session() as s:
        m = s.get(M.Match, f"m-{tag}")
        assert m.meta["header"]["server_name"] == "Valve CS2"
        assert m.meta["eye_position_check"]["ratio"] is None
        assert m.error == "boom  in error"
        p = s.get(M.Player, 76561190000000000 + len(tag))
        assert "\x00" not in p.last_known_name and len(p.last_known_name) == 128


def test_sqlite_round_trip(tmp_path):
    db = Database(f"sqlite:///{tmp_path}/s.sqlite")
    db.init_schema()
    _round_trip(db, "sqlite")


@pytest.mark.skipif(not os.environ.get("CS2A_TEST_PG_URL"), reason="set CS2A_TEST_PG_URL to test against PostgreSQL")
def test_postgres_round_trip():
    db = Database(os.environ["CS2A_TEST_PG_URL"])
    db.init_schema()
    _round_trip(db, f"pg{os.getpid()}")
