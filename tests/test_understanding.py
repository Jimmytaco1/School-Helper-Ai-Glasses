"""Questions are understood by rules and fuzzy matching, not by the model, so
they are fast and predictable. These tests use deliberately messy wording."""
import argparse

import pytest

import notebook_checker as nc
from conftest import make_image


def understood(con, q):
    return nc.understand(con, q)


def test_typos_nicknames_and_yesterday(con):
    i = understood(con, "sumarise yesterdays phisics")
    assert i["intent"] == "summary"
    assert i["filters"] == {"subject": "physics", "year": 2026, "term": 4, "week": 4, "day": 2}
    assert i["topic"] == []


def test_weak_points_with_misspelt_words(con):
    i = understood(con, "whats my weak points in enginering wek 4")
    assert i["intent"] == "weak"
    assert i["filters"] == {"subject": "engineering", "week": 4}


def test_number_words_and_multiword_subjects(con):
    i = understood(con, "show me maths methods week four term 4")
    assert i["intent"] == "show"
    assert i["filters"] == {"subject": "maths-methods", "week": 4, "term": 4}


def test_ordinals_and_nicknames(con):
    i = understood(con, "sumarise 4th week lit")
    assert i["filters"] == {"subject": "literature", "week": 4}


def test_relative_weeks(con):
    assert understood(con, "recap last week specialist")["filters"] == {
        "subject": "specialist-maths", "year": 2026, "term": 4, "week": 3}
    assert understood(con, "mistaks in litrature this week")["filters"] == {
        "subject": "literature", "year": 2026, "term": 4, "week": 4}


def test_weekday_names_mean_the_most_recent_one(con):
    f = understood(con, "wednesday physics")["filters"]
    assert (f["week"], f["day"]) == (4, 3)
    f = understood(con, "last monday physics")["filters"]
    assert (f["week"], f["day"]) == (3, 1)


def test_file_type_words(con):
    i = understood(con, "summarise the pdfs in physics")
    assert i["kind"] == "pdf" and i["filters"] == {"subject": "physics"}


def test_tutor_intent_and_topic(con):
    i = understood(con, "teach me projectile motion in physics")
    assert i["intent"] == "tutor" and i["topic"] == ["projectile", "motion"]
    assert i["filters"] == {"subject": "physics"}


def test_topic_only_question(con):
    i = understood(con, "what did we learn about momentum")
    assert i["intent"] == "summary" and i["topic"] == ["momentum"] and i["filters"] == {}


def test_plain_exact_wording_still_works(con):
    i = understood(con, "summarize day 3 of week 4 term 4 for physics 2026")
    assert i["filters"] == {"subject": "physics", "year": 2026, "term": 4, "week": 4, "day": 3}


def test_asking_finds_a_topic_despite_a_typo(con, model, tmp_path, capsys):
    (tmp_path / "a.txt").write_text("Projectile motion: horizontal velocity is constant.")
    (tmp_path / "b.txt").write_text("Shear force and bending moment diagrams for beams.")
    nc.store_file(con, tmp_path / "a.txt", nc.make_meta(_noon(), "physics"), check=False)
    nc.store_file(con, tmp_path / "b.txt", nc.make_meta(_noon(), "engineering"), check=False)

    nc.cmd_ask(con, argparse.Namespace(question="summarise projectle motion".split()))
    out = capsys.readouterr().out
    assert "1 note(s)" in out and "a bullet point summary" in out

    nc.cmd_ask(con, argparse.Namespace(question="show bending moment".split()))
    assert "Shear force" in capsys.readouterr().out


def test_unknown_questions_say_what_exists(con, model, capsys):
    nc.cmd_ask(con, argparse.Namespace(question="summarise physics today".split()))
    out = capsys.readouterr().out
    assert "No notes found" in out and "No notes stored yet" in out


def test_tutor_wording_outside_the_terminal_gives_a_hint(con, capsys):
    nc.cmd_ask(con, argparse.Namespace(question="teach me projectile motion in physics".split(),
                                       interactive=False))
    assert "Tutor tab" in capsys.readouterr().out


def _noon():
    import datetime as dt
    return dt.datetime(2026, 10, 28, 12)
