"""Steam chat message from the bot when a user's match has been analyzed (opt-in)."""

from datetime import datetime, timezone

import pandas as pd
from fastapi.testclient import TestClient

from cs2_analyzer.ingest.chat import map_label, match_message
from test_ingest import AUTH, CODES, SID, URL, FakeHistory, _app, _wait
from test_storage_api import _fake_result

H = {"Authorization": "Bearer svc"}
OTHER = 76561198000000001   # the other player in the fake match


def _result_with_user(match_id):
    r = _fake_result(match_id)
    me = r.match_stats.iloc[0].to_dict() | {"steam_id": SID, "name": "me"}
    r.match_stats = pd.concat([r.match_stats, pd.DataFrame([me])], ignore_index=True)
    return r


def test_message_text():
    s = {"matchId": "gc/1", "map": "de_mirage", "playedAt": datetime(2026, 9, 30, tzinfo=timezone.utc), "events": 3,
         "classes": {"NORMAL": 8, "ELEVATED": 1, "HIGH": 1}}
    text = match_message(s, "https://cheatscanner.eu")
    assert text.startswith("Your CS2 match on Mirage (30 Sep 2026) has been analyzed: 3 evidence events, "
                           "1 player ELEVATED, 1 player HIGH.")
    assert "https://cheatscanner.eu/#/matches/gc%2F1" in text and "not a verdict" in text
    quiet = match_message({"matchId": "m", "map": None, "events": 0, "classes": {"NORMAL": 10}}, "https://x")
    assert "no evidence events and no player with a raised evidence class" in quiet
    words = text.split("\n")[0] + text.split("\n")[2]
    assert "probab" not in words.lower() and "cheat" not in words.lower()
    assert map_label("cs_office") == "Office" and map_label("de_dust2") == "Dust2"


def test_opt_in_friend_check_and_outbox(config, tmp_path, monkeypatch):
    c, db, _ = _app(config, tmp_path, FakeHistory(), downloader=lambda url, dest, a, b: dest.write_bytes(b"x") or dest)
    c.put("/me/steam-match-access", json={"authCode": AUTH, "knownCode": CODES[0]})
    svc = TestClient(c.app)

    # Off by default; the bot isn't known until it has claimed once.
    assert c.get("/me/steam-chat").json() == {"enabled": False, "bot": None, "friends": None, "lastMessage": None}
    assert svc.get(f"/internal/chat/allowed/{SID}", headers=H).json() == {"allowed": False}
    assert svc.post("/internal/chat/claim", json={}).status_code == 401
    assert svc.post("/internal/chat/claim", headers=H, json={"botSteamId": "76561190000000009", "friends": []}).json() == []
    st = c.put("/me/steam-chat", json={"enabled": True}).json()
    assert st["enabled"] and st["bot"]["steamId"] == "76561190000000009" and st["friends"] is False
    assert svc.get(f"/internal/chat/allowed/{SID}", headers=H).json() == {"allowed": True}
    assert svc.get(f"/internal/chat/allowed/{OTHER}", headers=H).json() == {"allowed": False}   # not a user

    # The fetched match is analyzed: one message for the opted-in player, with the link and the findings.
    def fake_analyze(path, config, db=None, **kw):
        r = _result_with_user("gc-match")
        db.begin_match(r.meta, force=False, detector_version="d", scoring_version="s")
        db.save_results(r)
        db.mark_completed("gc-match", True)
        path.unlink()
        r.demo_deleted = True
        return r

    monkeypatch.setattr("cs2_analyzer.pipeline.analyze_demo", fake_analyze)
    job = svc.post("/internal/sharecodes/claim", headers=H).json()
    svc.post(f"/internal/sharecodes/{job['shareCode']}/result", headers=H, json={"demoUrl": URL})
    assert _wait(c) == "DONE"
    msgs = svc.post("/internal/chat/claim", headers=H, json={"friends": [str(SID)]}).json()
    assert len(msgs) == 1 and msgs[0]["steamId"] == str(SID)
    assert "on Mirage" in msgs[0]["text"] and "1 evidence event" in msgs[0]["text"]
    assert msgs[0]["text"].split("\n")[1].endswith("/#/matches/gc-match")
    assert svc.post("/internal/chat/claim", headers=H, json={}).json() == []          # claimed once
    assert c.get("/me/steam-chat").json()["friends"] is True

    # A send error is retried, then marked sent.
    assert svc.post(f"/internal/chat/{msgs[0]['id']}/result", headers=H, json={"error": "timeout"}).json()["status"] == "PENDING"
    again = svc.post("/internal/chat/claim", headers=H, json={}).json()
    assert [m["id"] for m in again] == [msgs[0]["id"]]
    assert svc.post(f"/internal/chat/{msgs[0]['id']}/result", headers=H, json={"sent": True}).json()["status"] == "SENT"
    assert c.get("/me/steam-chat").json()["lastMessage"]["status"] == "SENT"

    # Never twice for the same match; nothing for users who switched it off.
    assert db.queue_chat_messages("gc-match", "again") == 0
    c.put("/me/steam-chat", json={"enabled": False})
    r = _result_with_user("m2")
    db.begin_match(r.meta, force=False, detector_version="d", scoring_version="s")
    db.save_results(r)
    assert db.queue_chat_messages("m2", "x") == 0

    # Not friends with the bot: skipped, not retried.
    c.put("/me/steam-chat", json={"enabled": True})
    assert db.queue_chat_messages("m2", "x") == 1
    m = svc.post("/internal/chat/claim", headers=H, json={}).json()[0]
    assert svc.post(f"/internal/chat/{m['id']}/result", headers=H, json={"notFriend": True}).json()["status"] == "SKIPPED"
    assert svc.post("/internal/chat/claim", headers=H, json={}).json() == []
