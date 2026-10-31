import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import evaluate_reading as ev  # noqa: E402

from conftest import make_image  # noqa: E402


@pytest.mark.parametrize("a,b,expected", [
    ("", "", 0), ("abc", "abc", 0), ("abc", "", 3), ("", "abc", 3),
    ("kitten", "sitting", 3), ("v = u + at", "v = u + a t", 1),
])
def test_edit_distance(a, b, expected):
    assert ev.edit_distance(a, b) == expected


def test_cer_ignores_extra_spaces_and_line_breaks():
    assert ev.cer("v = u + at", "v  =  u +\nat") == 0
    assert ev.cer("abcd", "abxd") == pytest.approx(0.25)
    assert ev.cer("", "") == 0


def test_evaluate_a_folder_of_pairs(tmp_path):
    make_image(tmp_path / "p1.jpg")
    (tmp_path / "p1.txt").write_text("F = m a")
    make_image(tmp_path / "p2.jpg")
    (tmp_path / "p2.txt").write_text("p = m v")
    make_image(tmp_path / "p3.jpg")                    # no answer file: skipped
    readings = {"p1.jpg": "F = m a", "p2.jpg": "p = m u"}
    results = ev.evaluate(tmp_path, reader=lambda path: readings[path.name])
    assert [r["file"] for r in results] == ["p1.jpg", "p2.jpg"]
    assert [r["edits"] for r in results] == [0, 1]
    assert ev.overall(results) == pytest.approx(1 / 14)
    text = ev.report(results, "test-model")
    assert "test-model" in text and "Overall CER: 7.1%" in text
