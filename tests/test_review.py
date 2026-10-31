import datetime as dt
import json

import notebook_checker as nc
from conftest import FIXED_TODAY


def due_of(con, wid):
    return con.execute("select due from weak where id=?", (wid,)).fetchone()[0]


def weak_row(con, wid=1):
    return con.execute("select * from weak where id=?", (wid,)).fetchone()


def test_a_topic_is_only_added_once(con):
    assert nc.add_weak(con, "Physics", "Momentum", "", "p = m v") is True
    assert nc.add_weak(con, "physics", "momentum", "", "p = m v") is False


def test_new_items_are_due_tomorrow(con):
    nc.add_weak(con, "physics", "Momentum", "", "p = m v")
    assert due_of(con, 1) == (FIXED_TODAY + dt.timedelta(days=1)).isoformat()


def test_spacing_grows_when_you_remember_and_resets_when_you_forget(con):
    nc.add_weak(con, "physics", "Momentum", "", "p = m v")
    gaps = []
    for remembered in (True, True, True, False):
        due = nc.reschedule(con, weak_row(con), remembered)
        gaps.append((due - FIXED_TODAY).days)
    assert gaps == [1, 3, 8, 1]


def test_a_coming_test_pulls_the_review_forward(con):
    nc.add_weak(con, "physics", "Momentum", "", "p = m v")
    con.execute("update weak set reps=2, interval_days=3, ease=2.7")
    con.commit()
    nc.cmd_test(con, type("A", (), {"subject": "physics", "date": "2026-11-02", "name": "IA3 exam"}))
    due = nc.reschedule(con, weak_row(con), True)               # would be 8 days away
    assert due == dt.date(2026, 11, 1)                           # the day before the test


def test_reminders_show_due_items_and_coming_tests(con, capsys):
    nc.add_weak(con, "physics", "Momentum", "", "p = m v")
    con.execute("update weak set due=?", (FIXED_TODAY.isoformat(),))
    con.commit()
    nc.cmd_test(con, type("A", (), {"subject": "physics", "date": "2026-11-02", "name": "IA3 exam"}))
    nc.cmd_remind(con)
    out = capsys.readouterr().out
    assert "Due for review today: 1" in out and "Momentum" in out
    assert "TEST in 5 day(s): IA3 exam" in out


def test_tutor_session_saves_what_you_found_hard(con, monkeypatch):
    monkeypatch.setattr(nc, "chat_llm", lambda messages, as_json=False, ctx=8192: json.dumps(
        {"shaky": [{"topic": "Horizontal acceleration", "fix": "It is zero"}, {"topic": "", "fix": "x"}]}))
    messages = [{"role": "system", "content": "s"}, {"role": "user", "content": "q"},
                {"role": "assistant", "content": "a"}, {"role": "user", "content": "zero?"}]
    assert nc.tutor_wrapup(con, "physics", messages) == 1
    assert con.execute("select source from weak").fetchone()[0] == "tutor"
    assert nc.tutor_wrapup(con, "physics", messages[:2]) == 0   # too short to judge


def test_tutor_uses_your_own_notes_and_known_weak_points(con, tmp_path):
    (tmp_path / "a.txt").write_text("Projectile motion: horizontal velocity is constant.")
    nc.store_file(con, tmp_path / "a.txt", nc.make_meta(dt.datetime(2026, 10, 28, 12), "physics"), check=False)
    nc.add_weak(con, "physics", "Vectors", "", "magnitude and direction")
    messages, found = nc.tutor_start(con, "physics", "projectile motion")
    assert found
    system = messages[0]["content"]
    assert "horizontal velocity is constant" in system and "Vectors" in system
