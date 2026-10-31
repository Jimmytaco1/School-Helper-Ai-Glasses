"""The inbox watcher's decisions, tested without waiting for real file events."""
import datetime as dt
import os
import time

import pytest

pytest.importorskip("watchdog")

import notebook_checker as nc  # noqa: E402
import notebook_hub as hub  # noqa: E402
from conftest import make_image  # noqa: E402


@pytest.fixture
def inbox(env, model, monkeypatch):
    box = env / "inbox"
    box.mkdir()
    monkeypatch.setattr(hub, "INBOX", box)
    monkeypatch.setattr(hub, "wait_until_stable", lambda p, **k: True)
    return box


def subjects(con):
    return sorted(r[0] for r in con.execute("select subject from notes"))


def test_subject_from_a_subfolder(inbox, con):
    (inbox / "physics").mkdir()
    hub.handle(con, make_image(inbox / "physics" / "a.jpg"))
    assert subjects(con) == ["physics"]
    assert not (inbox / "physics" / "a.jpg").exists()           # moved into the cabinet


def test_subject_from_the_file_name(inbox, con):
    hub.handle(con, make_image(inbox / "specialist-maths_page2.jpg"))
    assert subjects(con) == ["specialist-maths"]


def test_subject_from_current_subject_file(inbox, con):
    (inbox / "current_subject.txt").write_text("Literature")
    hub.handle(con, make_image(inbox / "scan001.jpg"))
    assert subjects(con) == ["literature"]


def test_subject_guessed_by_the_model_or_unsorted(inbox, con, model):
    hub.handle(con, make_image(inbox / "random1.jpg"))
    model.subject_guess = "banana"
    hub.handle(con, make_image(inbox / "random2.jpg", exif_date="2026:10:14 09:00:00"))
    assert subjects(con) == ["physics", "unsorted"]


def test_date_written_on_the_page_decides_the_day(inbox, con):
    hub.handle(con, make_image(inbox / "physics_x.jpg"))        # fake model writes "Date: 21/10/2026"
    row = con.execute("select year, term, week, day from notes").fetchone()
    assert tuple(row) == (2026, 4, 3, 3)


def test_exact_repeats_are_removed_to_save_space(inbox, con):
    first = make_image(inbox / "physics_a.jpg")
    data = first.read_bytes()
    hub.handle(con, first)
    again = inbox / "physics_b.jpg"
    again.write_bytes(data)
    hub.handle(con, again)
    assert not again.exists() and not (inbox / "_duplicates").exists()
    assert con.execute("select count(*) from notes").fetchone()[0] == 1


def test_repeats_can_be_kept_if_you_prefer(inbox, con, monkeypatch):
    monkeypatch.setattr(hub, "KEEP_DUPLICATES", True)
    first = make_image(inbox / "physics_a.jpg")
    data = first.read_bytes()
    hub.handle(con, first)
    again = inbox / "physics_b.jpg"
    again.write_bytes(data)
    hub.handle(con, again)
    assert len(list((inbox / "_duplicates").iterdir())) == 1


def test_a_failure_leaves_the_file_in_the_inbox(inbox, con, monkeypatch):
    img = make_image(inbox / "physics_a.jpg")
    monkeypatch.setattr(nc, "extract_text", lambda p: (_ for _ in ()).throw(RuntimeError("down")))
    with pytest.raises(RuntimeError):
        hub.handle(con, img)
    assert img.exists()


def test_lecture_video_becomes_a_transcript_then_is_deleted(inbox, con, monkeypatch):
    (inbox / "physics").mkdir()
    video = inbox / "physics" / "lecture.mp4"
    video.write_bytes(b"not really a video")
    stamp = dt.datetime(2026, 10, 28, 12).timestamp()
    os.utime(video, (stamp, stamp))
    monkeypatch.setattr(hub, "transcribe", lambda p: "Today we cover conservation of energy. " * 20)
    monkeypatch.setattr(hub, "DELETE_VIDEO_AFTER", True)
    hub.handle(con, video)
    assert not video.exists()
    row = con.execute("select path, subject, week, day from notes").fetchone()
    assert row["path"].endswith("lecture.transcript.txt") and (row["subject"], row["week"], row["day"]) == ("physics", 4, 3)


def test_a_video_with_no_speech_is_kept(inbox, con, monkeypatch):
    video = inbox / "silent.mp4"
    video.write_bytes(b"x")
    monkeypatch.setattr(hub, "transcribe", lambda p: "   ")
    hub.handle(con, video)
    assert video.exists()


def test_youtube_link_files(inbox, con, monkeypatch):
    (inbox / "physics").mkdir()
    link = inbox / "physics" / "Lecture.url"
    link.write_text("[InternetShortcut]\nURL=https://www.youtube.com/watch?v=abcdefghijk\n")
    monkeypatch.setattr(nc, "youtube_text", lambda url: ("abcdefghijk", "energy is conserved " * 10))
    hub.handle(con, link)
    assert not link.exists() and subjects(con) == ["physics"]

    other = inbox / "physics" / "other.url"
    other.write_text("[InternetShortcut]\nURL=https://example.com/\n")
    hub.handle(con, other)
    assert other.exists()                                        # not YouTube: left alone


def test_which_files_the_watcher_cares_about(inbox):
    (inbox / "_duplicates").mkdir()
    assert hub.wanted(inbox / "a.jpg") and hub.wanted(inbox / "physics" / "A.PNG")
    assert not hub.wanted(inbox / "a.jpg.part")                  # half-written file
    assert not hub.wanted(inbox / "_duplicates" / "a.jpg")
    assert not hub.wanted(inbox / "~lock.pdf")
    assert not hub.wanted(inbox / "notes.docx")


def test_wait_until_stable_waits_for_the_file_to_stop_growing(tmp_path):
    f = tmp_path / "x.bin"
    f.write_bytes(b"data")
    assert hub.wait_until_stable(f, checks=2, delay=0.01, timeout=2) is True
    assert hub.wait_until_stable(tmp_path / "missing", checks=2, delay=0.01, timeout=0.1) is False


def test_setup_makes_every_folder_and_is_safe_to_repeat(env, monkeypatch):
    box = env / "inbox"
    monkeypatch.setattr(hub, "INBOX", box)
    hub.setup_folders("2026")
    hub.setup_folders("2026")
    assert all((box / s).is_dir() for s in hub.SUBJECTS)
    days = list((env / "cabinet" / "2026").glob("*/term*/week*/day*"))
    assert len(days) == len(hub.SUBJECTS) * 40 * 5               # 40 term weeks, Monday to Friday
    assert (env / "cabinet" / "2026" / "physics" / "term4" / "week04" / "day3").is_dir()


def test_inbox_and_notebook_must_be_separate_folders(env, monkeypatch):
    monkeypatch.setattr(hub, "INBOX", nc.ROOT)
    with pytest.raises(SystemExit):
        hub.check_folders()
    monkeypatch.setattr(hub, "INBOX", nc.ROOT / "inside")
    with pytest.raises(SystemExit):
        hub.check_folders()
    monkeypatch.setattr(hub, "INBOX", env / "elsewhere")
    hub.check_folders()
