import datetime as dt

import pytest

import notebook_checker as nc
import notebook_config as cfg
from conftest import FIXED_TODAY, make_image

D = dt.date


@pytest.mark.parametrize("text,expected", [
    ("Date: 21/10/2026\nphysics", D(2026, 10, 21)),
    ("Wed 21 Oct\nprojectile", D(2026, 10, 21)),
    ("October 21st notes", D(2026, 10, 21)),
    ("21.10.26 momentum", D(2026, 10, 21)),
    ("Physics 3/4 of the distance", None),     # a fraction is not a date
    ("Mark 5 solution", None),                 # 'Mar' must not match 'Mark'
    ("Due 30 October", None),                  # in the future, so not a note date
    ("no date here", None),
    ("31/2/2026 impossible", None),
])
def test_date_from_text(text, expected):
    assert nc.date_from_text(text, ref=FIXED_TODAY) == expected


def test_file_date_prefers_file_name(env, tmp_path):
    img = make_image(tmp_path / "physics_2026-10-19.jpg")
    when, source = nc.file_date(img, "Date: 21/10/2026")
    assert when.date() == D(2026, 10, 19) and source == "the file name"


def test_file_date_then_page_then_exif_then_file_date(env, tmp_path):
    plain = make_image(tmp_path / "page.jpg")
    when, source = nc.file_date(plain, "Date: 21/10/2026")
    assert when.date() == D(2026, 10, 21) and "page" in source

    exif = make_image(tmp_path / "photo.jpg", exif_date="2026:10:15 10:10:10")
    when, source = nc.file_date(exif, "no date in the text")
    assert when.date() == D(2026, 10, 15) and "photo" in source

    when, source = nc.file_date(plain, "no date in the text")
    assert source == "the file's date"


def test_pdfs_ignore_dates_written_in_their_text(env, tmp_path):
    pdf = tmp_path / "worksheet.pdf"
    pdf.write_bytes(b"%PDF-1.4")
    _, source = nc.file_date(pdf, "Date: 21/10/2026")
    assert source == "the file's date"


@pytest.mark.parametrize("day,expected", [
    (D(2026, 10, 28), (4, 4, 3)),     # Wednesday of term 4, week 4
    (D(2026, 10, 6), (4, 1, 2)),      # first day of term 4 (a Tuesday)
    (D(2026, 12, 20), (4, 10, 7)),    # holidays after term 4 clamp to the last week
    (D(2026, 1, 10), (1, 1, 6)),      # before term 1 clamps to week 1
    (D(2027, 4, 12), (2, 1, 1)),
])
def test_term_week_day(day, expected):
    assert cfg.term_week_day(day) == expected


def test_a_subject_can_have_its_own_calendar(monkeypatch):
    uni = [(1, D(2028, 9, 4), D(2028, 12, 15)), (2, D(2029, 2, 12), D(2029, 5, 25))]
    monkeypatch.setitem(cfg.CALENDARS, "uni-calculus", uni)
    assert cfg.term_week_day(D(2028, 9, 20), "uni-calculus") == (1, 3, 3)
    assert cfg.term_week_day(D(2029, 3, 1), "uni-calculus") == (2, 3, 4)
    assert cfg.term_week_day(D(2026, 10, 28), "physics") == (4, 4, 3)
