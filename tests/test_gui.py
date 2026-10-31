"""The desktop window, driven without a person. Needs a display; on a computer
without one (or without Tkinter) these tests are skipped. On Linux run them
with:  xvfb-run -a pytest"""
import datetime as dt
import gc
import json
import sys
import threading
import time
from pathlib import Path

import pytest

tk = pytest.importorskip("tkinter")

import notebook_checker as nc  # noqa: E402
import notebook_gui as gui  # noqa: E402
from conftest import make_image  # noqa: E402

NOON = dt.datetime(2026, 10, 28, 12)


def pump(app, until=None, timeout=10):
    """Let the window do its work until the background job has finished."""
    until = until or (lambda: not app.busy)
    end = time.time() + timeout
    while time.time() < end:
        app.update()
        if until():
            break
        time.sleep(0.01)
    app.update()
    assert until(), "the window was still busy after the timeout"


def read(box):
    return box.get("1.0", "end")


@pytest.fixture
def app(env, model, monkeypatch):
    try:
        a = gui.App(watch=False)
    except tk.TclError:
        pytest.skip("no display available for the desktop window")
    yield a
    try:
        a.destroy_app()
    except tk.TclError:
        pass
    del a
    gc.collect()                      # tidy Tk objects on this thread, not a background one


@pytest.fixture
def con(env):
    c = nc.connect()
    yield c
    c.close()


def add_note(con, tmp_path, text="Projectile motion: horizontal velocity is constant."):
    f = tmp_path / "a.txt"
    f.write_text(text)
    return nc.store_file(con, f, nc.make_meta(NOON, "physics"), check=False)


def test_window_opens_with_every_tab(app):
    tabs = [app.nb.tab(t, "text") for t in app.nb.tabs()]
    assert tabs == ["Ask", "Add notes", "Tutor", "Practice test", "Review",
                    "Tests and weak points", "Storage", "Activity", "Help"]
    assert "0 notes" in app.summary.get()


def test_asking_a_messy_question(app, con, tmp_path):
    add_note(con, tmp_path)
    app.ask_entry.insert(0, "sumarise todays phisics")
    app.ask()
    pump(app)
    out = read(app.ask_out)
    assert "You: sumarise todays phisics" in out and "1 note(s)" in out and "a bullet point summary" in out


def test_a_second_request_while_busy_is_politely_refused(app):
    app.busy = True
    assert app.run_job(lambda c: None, lambda *a: None) is False
    assert "busy" in app.status.get().lower()
    app.busy = False


def test_filing_photos_and_pdfs(app, con, tmp_path):
    img = make_image(tmp_path / "page.jpg")
    app.add_subject.set("physics")
    app.file_batch([str(img)])
    pump(app)
    out = read(app.add_out)
    assert "filed under physics" in out and "term 4 week 3 day 3" in out and "some feedback" in out
    assert con.execute("select count(*) from notes").fetchone()[0] == 1
    app.file_batch([str(img)])
    pump(app)
    assert "already stored" in read(app.add_out)


def test_a_bad_date_is_explained_not_a_crash(app, monkeypatch, tmp_path):
    shown = []
    monkeypatch.setattr(gui.messagebox, "showinfo", lambda *a, **k: shown.append(a))
    app.auto_date.set(False)
    app.add_date.delete(0, "end")
    app.add_date.insert(0, "tomorrow")
    app.file_batch([str(make_image(tmp_path / "p.jpg"))])
    assert shown and not app.busy


def test_youtube_links(app, con, monkeypatch):
    monkeypatch.setattr(nc, "youtube_text", lambda url: ("dQw4w9WgXcQ", "energy is conserved " * 10))
    app.add_subject.set("physics")
    app.yt_entry.insert(0, "https://youtu.be/dQw4w9WgXcQ")
    app.youtube()
    pump(app)
    shown = read(app.add_out)
    assert "====" not in shown                                           # divider lines are tidied away
    assert "a bullet point summary" in shown and "Filed: physics" in shown
    assert con.execute("select count(*) from notes").fetchone()[0] == 1


def test_a_tutor_session(app, con, monkeypatch, tmp_path):
    add_note(con, tmp_path)
    replies = iter(["Big idea... what is the horizontal acceleration?", "Good."])

    def chat(messages, as_json=False, ctx=None):
        if as_json:
            return json.dumps({"shaky": [{"topic": "Horizontal acceleration", "fix": "It is zero"}]})
        return next(replies)
    monkeypatch.setattr(nc, "chat_llm", chat)
    app.t_subject.set("physics")
    app.t_topic.insert(0, "projectile motion")
    app.tutor_start()
    pump(app)
    assert "(using your notes)" in read(app.t_out) and "Tutor: Big idea" in read(app.t_out)
    app.t_answer.insert(0, "zero")
    app.tutor_send()
    pump(app)
    assert "You: zero" in read(app.t_out) and "Tutor: Good." in read(app.t_out)
    app.tutor_finish()
    pump(app)
    assert "1 idea(s) added" in read(app.t_out)
    assert con.execute("select source from weak").fetchone()[0] == "tutor"


def test_reviewing(app, con):
    nc.add_weak(con, "physics", "Momentum", "wrote p = m/v", "p = m v")
    con.execute("update weak set due=?", (nc.today().isoformat(),))
    con.commit()
    app.load_review()
    assert "Momentum" in app.rv_topic.cget("text") and app.rv_answer.cget("text") == ""
    app.review_show()
    assert app.rv_answer.cget("text") == "p = m v"
    app.review_mark(True)
    assert con.execute("select reps from weak").fetchone()[0] == 1
    assert "Nothing due" in app.rv_topic.cget("text")


def test_saving_a_test_and_a_weak_point(app, con):
    app.te_name.insert(0, "IA3 exam")
    app.te_date.delete(0, "end")
    app.te_date.insert(0, "2026-11-12")
    app.save_test()
    assert con.execute("select name, date from tests").fetchone()[:] == ("IA3 exam", "2026-11-12")
    assert "IA3 exam in 15 day(s)" in app.summary.get()
    app.wk_topic.insert(0, "Range formula")
    app.wk_fix.insert(0, "R = u^2 sin 2θ / g")
    app.save_weak()
    assert len(app.tree.get_children()) == 1


def test_storage_tab_reports_and_previews(app):
    assert "Notes folder:" in read(app.st_out)
    app.storage_tidy(False, False)
    pump(app)
    assert "0 photo(s) older than 60 days would be shrunk" in read(app.st_out)


def test_background_prints_go_to_the_activity_tab(app):
    sys.stdout = app.router           # (pytest swaps stdout between test phases; the real app keeps it)
    threading.Thread(target=lambda: print("the watcher says hello")).start()
    pump(app, until=lambda: "watcher says hello" in read(app.activity), timeout=3)


def test_logo_and_banner_are_used_when_present(env, model, tmp_path, monkeypatch):
    from PIL import Image
    assets = tmp_path / "assets"
    assets.mkdir()
    Image.new("RGB", (64, 64), "blue").save(assets / "logo.png")
    Image.new("RGB", (900, 100), "green").save(assets / "banner.jpg")
    monkeypatch.setattr(gui, "ASSETS", assets)
    try:
        a = gui.App(watch=False)
    except tk.TclError:
        pytest.skip("no display available for the desktop window")
    try:
        assert len(a._pics) >= 2
    finally:
        a.destroy_app()


def test_closing_restores_normal_printing(env, model):
    before = sys.stdout
    try:
        a = gui.App(watch=False)
    except tk.TclError:
        pytest.skip("no display available for the desktop window")
    assert sys.stdout is not before
    a.destroy_app()
    assert sys.stdout is before


def test_making_a_practice_test(app, con, model, tmp_path, env):
    pytest.importorskip("docx")
    add_note(con, tmp_path)
    model.json_reply = lambda prompt: {"questions": [
        {"type": "short_answer", "question": "State the vertical acceleration of a projectile.",
         "answer": "9.8 m/s^2 down", "explanation": "Gravity.", "marks": 2},
        {"type": "calculation", "question": "A ball moves at 10 m/s for 2 s. How far does it go?",
         "answer": "20 m", "explanation": "d = vt", "marks": 3}]}
    app.q_request.delete(0, "end")
    app.q_request.insert(0, "physics projectile motion")
    app.q_count.set(2)
    app.quiz_make()
    pump(app)
    out = read(app.q_out)
    assert "Made a test with 2 questions (5 marks)" in out and "answer key is on the last pages" in out
    assert app.q_last.exists() and app.q_last.parent == env / "cabinet" / "Practice tests"
    assert "disabled" not in app.q_open.state()


def test_a_test_with_no_notes_explains_itself(app):
    pytest.importorskip("docx")
    app.q_request.delete(0, "end")
    app.q_request.insert(0, "physics week 4")
    app.quiz_make()
    pump(app)
    assert "couldn't find any notes" in read(app.q_out)
