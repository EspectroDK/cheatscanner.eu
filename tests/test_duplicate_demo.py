"""A demo of an analyzed match grants access to it, so a duplicate must be the same demo, not just the same file name."""

import hashlib

import pytest

from cs2_analyzer import pipeline
from cs2_analyzer.storage import models as M
from cs2_analyzer.storage.repository import AlreadyProcessedError, Database

MATCH = "3846262246439125265"


class _Parsed(Exception):
    """Stands in for parsing: records which match id the demo would be analyzed under."""


def _db(tmp_path, demo: bytes) -> Database:
    db = Database(f"sqlite:///{tmp_path}/dup.sqlite")
    db.init_schema()
    with db.session() as s:
        s.add(M.Match(match_id=MATCH, demo_sha256=hashlib.sha256(demo).hexdigest(), processing_status="COMPLETED"))
    return db


@pytest.fixture
def parse_spy(monkeypatch):
    seen = {}

    class Parser:
        def parse(self, path, match_id=None):
            seen["match_id"] = match_id
            raise _Parsed()

    monkeypatch.setattr(pipeline, "get_parser", lambda backend: Parser())
    return seen


def test_same_demo_is_a_duplicate(config, tmp_path, parse_spy):
    demo = b"the real demo"
    db = _db(tmp_path, demo)
    path = tmp_path / f"anything_match730_{MATCH}_0_0.dem"
    path.write_bytes(demo)
    with pytest.raises(AlreadyProcessedError) as exc:
        pipeline.analyze_demo(path, config, db=db, keep_demo=True)
    assert exc.value.match_id == MATCH and "match_id" not in parse_spy


def test_renamed_file_does_not_claim_the_match(config, tmp_path, parse_spy):
    db = _db(tmp_path, b"the real demo")
    fake = b"x"
    path = tmp_path / f"upload_match730_{MATCH}_0_0.dem"
    path.write_bytes(fake)
    with pytest.raises(_Parsed):
        pipeline.analyze_demo(path, config, db=db, keep_demo=True)
    # Analyzed as a match of its own (by content), never as a duplicate of MATCH.
    assert parse_spy["match_id"] == f"sha256-{hashlib.sha256(fake).hexdigest()[:24]}"
