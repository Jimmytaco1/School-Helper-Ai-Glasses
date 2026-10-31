"""Drives the browser app without a browser, using Streamlit's AppTest."""
import datetime as dt
import json
from pathlib import Path

import pytest

pytest.importorskip("streamlit")
from streamlit.testing.v1 import AppTest  # noqa: E402

import notebook_checker as nc  # noqa: E402

APP = str(Path(__file__).resolve().parent.parent / "notebook_app.py")


@pytest.fixture
def app(con, model, monkeypatch, tmp_path):
    (tmp_path / "a.txt").write_text("Projectile motion: horizontal velocity is constant.")
    nc.store_file(con, tmp_path / "a.txt", nc.make_meta(dt.datetime(2026, 10, 28, 12), "physics"), check=False)
    replies = iter(["Big idea... what is the horizontal acceleration?", "Good.", "Great."])

    def chat(messages, as_json=False, ctx=None):
        if as_json:
            return json.dumps({"shaky": [{"topic": "Horizontal acceleration", "fix": "It is zero"}]})
        return next(replies)
    monkeypatch.setattr(nc, "chat_llm", chat)
    at = AppTest.from_file(APP, default_timeout=60)
    at.run()
    return at


def click(at, label):
    next(b for b in at.button if b.label == label or b.label.startswith(label)).click()
    at.run()


def test_the_app_starts_with_all_tabs(app):
    assert not app.exception
    assert [t.label for t in app.tabs] == ["Ask", "Add notes", "Tutor", "Review", "Tests and weak points"]


def test_asking_a_messy_question(app):
    app.text_input(key="ask_text").set_value("sumarise todays phisics")
    click(app, "Ask")
    assert not app.exception
    reply = [m.markdown[0].value for m in app.chat_message][-1]
    assert "1 note(s)" in reply and "a bullet point summary" in reply


def test_a_tutor_session_from_start_to_finish(app, con):
    app.selectbox(key="t_subject").select("physics")
    app.text_input(key="t_topic").set_value("projectile motion")
    click(app, "Start")
    assert not app.exception and len(app.chat_message) == 1
    app.text_input(key="t_answer").set_value("zero")
    click(app, "Send")
    assert len(app.chat_message) == 3
    click(app, "Finish")
    assert any("1 idea" in s.value for s in app.success)
    assert con.execute("select count(*) from weak where source='tutor'").fetchone()[0] == 1


def test_reviewing_a_weak_point(app, con):
    nc.add_weak(con, "physics", "Momentum", "", "p = m v")
    con.execute("update weak set due=?", (nc.today().isoformat(),))
    con.commit()
    app.run()
    click(app, "Show the answer")
    assert any("p = m v" in i.value for i in app.info)
    click(app, "I remembered it")
    assert not app.exception
    assert con.execute("select reps from weak").fetchone()[0] == 1


def test_saving_a_test_shows_it_in_the_sidebar(app):
    app.text_input(key="test_name").set_value("IA3 exam")
    click(app, "Save test")
    assert not app.exception
    assert any("IA3 exam" in m.value for m in app.sidebar.markdown)


def test_logo_and_banner_are_picked_up_from_the_assets_folder(con, model, tmp_path):
    import shutil
    from PIL import Image
    app_dir = tmp_path / "appcopy"
    (app_dir / "assets").mkdir(parents=True)
    shutil.copy(APP, app_dir / "notebook_app.py")
    Image.new("RGB", (64, 64), "blue").save(app_dir / "assets" / "logo.png")
    Image.new("RGB", (800, 100), "green").save(app_dir / "assets" / "banner.jpg")
    at = AppTest.from_file(str(app_dir / "notebook_app.py"), default_timeout=60)
    at.run()
    assert not at.exception
    assert len(at.get("image")) == 2                # the banner and the sidebar logo

    plain = tmp_path / "plain"
    plain.mkdir()
    shutil.copy(APP, plain / "notebook_app.py")      # no assets folder: nothing extra shown
    at2 = AppTest.from_file(str(plain / "notebook_app.py"), default_timeout=60)
    at2.run()
    assert not at2.exception and len(at2.get("image")) == 0
