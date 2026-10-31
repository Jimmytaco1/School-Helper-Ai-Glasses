"""Keeping disk use small: shrinking photos, no leftover temp files, reports, tidy."""
import argparse
import datetime as dt
import random
import tempfile
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

import notebook_checker as nc

NOON = dt.datetime(2026, 10, 28, 12)


def phone_photo(path, size=(4000, 3000), quality=97):
    """Something like a phone photo of a page: lots of pixels, thin dark lines."""
    img = Image.new("RGB", size, "white")
    draw = ImageDraw.Draw(img)
    rnd = random.Random(1)
    for _ in range(400):
        x, y = rnd.randrange(size[0]), rnd.randrange(size[1])
        draw.line((x, y, x + rnd.randrange(300), y + rnd.randrange(-20, 20)), fill="black", width=6)
    img.save(path, "JPEG", quality=quality)
    return Path(path)


def meta(subject="physics"):
    return nc.make_meta(NOON, subject)


def test_filing_a_photo_stores_a_much_smaller_copy(con, model, env, tmp_path):
    src = phone_photo(tmp_path / "IMG_0001.jpg")
    original = src.stat().st_size
    nid = nc.store_file(con, src, meta(), check=False, move=True)
    stored = Path(con.execute("select path from notes where id=?", (nid,)).fetchone()[0])
    assert not src.exists()                                    # moved out of the inbox
    assert stored.stat().st_size < original / 3
    with Image.open(stored) as im:
        assert max(im.size) <= nc.PHOTO_MAX_SIDE


def test_copying_leaves_your_own_original_alone(con, model, tmp_path):
    src = phone_photo(tmp_path / "keep_me.jpg")
    before = src.read_bytes()
    nc.store_file(con, src, meta(), check=False, move=False)
    assert src.read_bytes() == before


def test_the_original_is_still_recognised_as_a_repeat(con, model, tmp_path):
    src = phone_photo(tmp_path / "a.jpg")
    data = src.read_bytes()
    assert nc.store_file(con, src, meta(), check=False, move=True)
    again = tmp_path / "b.jpg"
    again.write_bytes(data)
    assert nc.already_stored(con, again)
    assert nc.store_file(con, again, meta(), check=False) is None


def test_scan_does_not_refile_shrunk_photos(con, model, tmp_path):
    nc.store_file(con, phone_photo(tmp_path / "a.jpg"), meta(), check=False, move=True)
    nc.cmd_scan(con, argparse.Namespace(no_check=True))
    assert con.execute("select count(*) from notes").fetchone()[0] == 1


def test_a_photo_that_would_not_get_smaller_is_kept_as_it_is(con, model, tmp_path):
    src = tmp_path / "tiny.jpg"
    Image.new("RGB", (40, 40), "white").save(src, quality=20, optimize=True)
    before = src.read_bytes()
    nid = nc.store_file(con, src, meta(), check=False, move=False)
    stored = Path(con.execute("select path from notes where id=?", (nid,)).fetchone()[0])
    assert stored.read_bytes() == before


def test_compression_can_be_switched_off(con, model, tmp_path, monkeypatch):
    monkeypatch.setattr(nc, "COMPRESS_PHOTOS", False)
    src = phone_photo(tmp_path / "a.jpg", size=(1000, 800))
    before = src.read_bytes()
    nid = nc.store_file(con, src, meta(), check=False, move=False)
    assert Path(con.execute("select path from notes where id=?", (nid,)).fetchone()[0]).read_bytes() == before


def test_transparent_pngs_become_white_not_black(tmp_path):
    src = tmp_path / "t.png"
    Image.new("RGBA", (50, 50), (0, 0, 0, 0)).save(src)
    out = nc.compress_image(src, tmp_path / "t.jpg")
    with Image.open(out) as im:
        assert im.getpixel((10, 10))[0] > 240


def test_reading_leaves_no_temp_files_behind(model, tmp_path, monkeypatch):
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(scratch))
    nc.extract_text(phone_photo(tmp_path / "a.jpg", size=(1200, 900)))
    try:
        import pymupdf as fitz
    except ImportError:
        import fitz
    doc = fitz.open()
    page = doc.new_page()
    page.insert_image(page.rect, filename=str(phone_photo(tmp_path / "b.jpg", size=(800, 600))))
    pdf = tmp_path / "scan.pdf"
    doc.save(pdf)
    nc.extract_text(pdf)                                       # a scanned page goes through the model
    assert list(scratch.iterdir()) == []


def test_pdfs_survive_the_tidy_up_and_stay_readable(con, model, tmp_path):
    try:
        import pymupdf as fitz
    except ImportError:
        import fitz
    doc = fitz.open()
    doc.new_page().insert_text((72, 72), "Projectile motion: horizontal velocity is constant.")
    pdf = tmp_path / "notes.pdf"
    doc.save(pdf)
    nid = nc.store_file(con, pdf, meta(), check=False, move=True)
    stored = Path(con.execute("select path from notes where id=?", (nid,)).fetchone()[0])
    assert stored.exists() and "horizontal velocity" in fitz.open(stored)[0].get_text()


def test_storage_report_adds_things_up(con, model, tmp_path):
    nc.store_file(con, phone_photo(tmp_path / "a.jpg"), meta("physics"), check=False, move=True)
    (tmp_path / "n.txt").write_text("some notes " * 100)
    nc.store_file(con, tmp_path / "n.txt", meta("engineering"), check=False)
    report = nc.storage_report()
    assert "2 file(s)" in report and "photos:" in report and "text and transcripts:" in report
    assert "physics:" in report and "engineering:" in report and "Biggest files:" in report


def test_human_sizes():
    assert nc.human(500) == "500 B" and nc.human(2048) == "2.0 KB" and nc.human(5 * 1024 ** 2) == "5.0 MB"


def test_tidy_previews_first_then_shrinks_only_old_photos(con, model, env, tmp_path, monkeypatch):
    monkeypatch.setattr(nc, "COMPRESS_PHOTOS", False)          # file them full size, to give tidy something to do
    old = nc.store_file(con, phone_photo(tmp_path / "old.jpg", size=(3000, 2200)), meta(), check=False, move=True)
    new = nc.store_file(con, phone_photo(tmp_path / "new.jpg", size=(3000, 2201)), meta(), check=False, move=True)
    con.execute("update notes set added=? where id=?", ("2026-07-01T10:00:00", old))
    con.commit()
    old_path = Path(con.execute("select path from notes where id=?", (old,)).fetchone()[0])
    new_path = Path(con.execute("select path from notes where id=?", (new,)).fetchone()[0])
    old_before, new_before = old_path.stat().st_size, new_path.stat().st_size

    preview = nc.tidy_photos(con, days=60)
    assert "would be shrunk" in preview and "add --apply" in preview
    assert old_path.stat().st_size == old_before                # a preview changes nothing

    done = nc.tidy_photos(con, days=60, apply=True)
    assert "1 photo(s)" in done and "shrunk" in done
    assert old_path.stat().st_size < old_before / 2
    assert new_path.stat().st_size == new_before                # recent photos are left alone
    assert con.execute("select count(*) from notes").fetchone()[0] == 2


def test_tidy_can_remove_old_photos_but_keeps_the_text(con, model, tmp_path):
    nid = nc.store_file(con, phone_photo(tmp_path / "old.jpg"), meta(), check=False, move=True, text="v = u + a t")
    con.execute("update notes set added='2026-01-05T09:00:00'")
    con.commit()
    path = Path(con.execute("select path from notes").fetchone()[0])
    nc.tidy_photos(con, days=30, delete=True, apply=True)
    assert not path.exists()
    assert con.execute("select text from notes where id=?", (nid,)).fetchone()[0] == "v = u + a t"


def test_storage_and_tidy_commands_run_from_the_chat_parser(con, model, capsys):
    args = nc.build_parser().parse_args(["tidy", "--days", "30"])
    args.fn(con, args)
    assert "0 photo(s)" in capsys.readouterr().out
    args = nc.build_parser().parse_args(["storage"])
    args.fn(con, args)
    assert "Notes folder:" in capsys.readouterr().out
