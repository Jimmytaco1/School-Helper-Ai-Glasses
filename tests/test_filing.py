import argparse
import datetime as dt

import pytest

import notebook_checker as nc
from conftest import make_image

NOON = dt.datetime(2026, 10, 28, 12)


def meta(subject="physics"):
    return nc.make_meta(NOON, subject)


def test_store_file_files_it_by_year_subject_term_week_day(con, env, tmp_path):
    src = tmp_path / "a.txt"
    src.write_text("Energy is conserved.")
    nid = nc.store_file(con, src, meta(), check=False, move=True)
    target = env / "cabinet" / "2026" / "physics" / "term4" / "week04" / "day3" / "a.txt"
    assert nid and target.exists() and not src.exists()
    row = con.execute("select * from notes where id=?", (nid,)).fetchone()
    assert (row["year"], row["subject"], row["term"], row["week"], row["day"]) == (2026, "physics", 4, 4, 3)


def test_identical_content_is_stored_once(con, tmp_path):
    (tmp_path / "a.txt").write_text("same words")
    (tmp_path / "b.txt").write_text("same words")
    assert nc.store_file(con, tmp_path / "a.txt", meta(), check=False)
    assert nc.store_file(con, tmp_path / "b.txt", meta(), check=False) is None
    assert con.execute("select count(*) from notes").fetchone()[0] == 1


def test_a_failed_read_never_loses_the_original(con, tmp_path, monkeypatch):
    src = tmp_path / "page.txt"
    src.write_text("important")
    monkeypatch.setattr(nc, "extract_text", lambda p: (_ for _ in ()).throw(RuntimeError("model down")))
    with pytest.raises(RuntimeError):
        nc.store_file(con, src, meta(), check=False, move=True)
    assert src.exists()
    assert con.execute("select count(*) from notes").fetchone()[0] == 0


def test_file_page_reports_where_it_went(con, model, tmp_path):
    img = make_image(tmp_path / "page.jpg")
    res = nc.file_page(con, img, "physics")                    # model says "Date: 21/10/2026"
    assert res["status"] == "filed"
    assert (res["meta"]["term"], res["meta"]["week"], res["meta"]["day"]) == (4, 3, 3)
    assert "page" in res["source"] and res["feedback"] == "some feedback"
    assert nc.file_page(con, img, "physics")["status"] == "duplicate"


def test_file_page_can_guess_the_subject(con, model, tmp_path):
    res = nc.file_page(con, make_image(tmp_path / "x.jpg"))
    assert res["meta"]["subject"] == "physics"
    model.subject_guess = "banana"
    res = nc.file_page(con, make_image(tmp_path / "y.jpg", exif_date="2026:10:14 09:00:00"))
    assert res["meta"]["subject"] == "unsorted"


def test_add_command_with_an_explicit_date(con, model, tmp_path):
    pdf = tmp_path / "Projectile Motion.txt"
    pdf.write_text("Projectile motion notes")
    args = nc.build_parser().parse_args(
        ["add", str(pdf), "--subject", "physics", "--date", "2026-10-21", "--no-check"])
    args.fn(con, args)
    row = con.execute("select term, week, day from notes").fetchone()
    assert tuple(row) == (4, 3, 3)


def test_scan_reads_files_dropped_into_the_folders_by_hand(con, model, env):
    folder = nc.note_dir(2026, "physics", 4, 2, 3)
    folder.mkdir(parents=True)
    (folder / "a.txt").write_text("KE = 1/2 m v^2")
    nc.cmd_scan(con, argparse.Namespace(no_check=True))
    row = con.execute("select subject, term, week, day from notes").fetchone()
    assert tuple(row) == ("physics", 4, 2, 3)


def test_unreadable_checker_output_is_skipped_not_fatal(con, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(nc, "llm", lambda *a, **k: "this is not json")
    (tmp_path / "a.txt").write_text("notes")
    nc.store_file(con, tmp_path / "a.txt", meta(), check=True)
    assert "unreadable" in capsys.readouterr().out
    assert con.execute("select count(*) from notes").fetchone()[0] == 1


def test_confident_mistakes_become_review_items(con, model, tmp_path):
    model.json_reply = {"summary": "s", "feedback": "f", "mistakes": [
        {"topic": "Signs", "issue": "wrote p = m/v", "fix": "p = m v"},
        {"topic": "No fix given", "issue": "x"},               # incomplete: ignored
        "not even a dict"]}
    (tmp_path / "a.txt").write_text("p = m / v")
    nc.store_file(con, tmp_path / "a.txt", meta(), check=True)
    rows = con.execute("select topic, fix, source from weak").fetchall()
    assert [tuple(r) for r in rows] == [("Signs", "p = m v", "checker")]


def test_summaries_are_cached(con, model, tmp_path):
    (tmp_path / "a.txt").write_text("some notes about energy")
    nc.store_file(con, tmp_path / "a.txt", meta(), check=False)
    rows = nc.select_notes(con, {"subject": "physics"})
    first = nc.summarise(con, rows)
    calls = len(model.prompts)
    assert nc.summarise(con, rows) == first and len(model.prompts) == calls


def test_long_text_is_split_into_chunks():
    pieces = nc.chunks("word " * 2500, words=1200)
    assert len(pieces) == 3 and all(len(p.split()) <= 1200 for p in pieces)
