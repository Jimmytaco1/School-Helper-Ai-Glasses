"""Practice tests: the model writes questions, the code checks them and builds the Word file."""
import argparse
import datetime as dt

import pytest

docx = pytest.importorskip("docx")

import notebook_checker as nc  # noqa: E402

NOON = dt.datetime(2026, 10, 28, 12)

GOOD = [
    {"type": "multiple_choice", "question": "What stays constant in projectile motion?",
     "options": ["A) Horizontal velocity", "B) Vertical velocity", "C) Speed", "D) Height"],
     "answer": "A", "explanation": "There is no horizontal force.", "marks": 1},
    {"type": "short_answer", "question": "State the vertical acceleration of a projectile.",
     "answer": "9.8 m/s^2 downwards", "explanation": "Only gravity acts.", "marks": 2},
    {"type": "calculation", "question": "A ball is thrown at 10 m/s horizontally for 2 s. How far does it go?",
     "answer": "20 m", "explanation": "d = vt = 10 x 2 = 20 m", "marks": 3},
]


def text_of(path):
    d = docx.Document(str(path))
    parts = [p.text for p in d.paragraphs]
    for t in d.tables:
        for row in t.rows:
            parts += [c.text for c in row.cells]
    for s in d.sections:
        parts += [p.text for p in s.footer.paragraphs]
    return "\n".join(parts)


def add_notes(con, tmp_path, text="Projectile motion: horizontal velocity is constant. Vertical acceleration is g."):
    f = tmp_path / "a.txt"
    f.write_text(text)
    nc.store_file(con, f, nc.make_meta(NOON, "physics"), check=False)


# ---------------------------------------------------------------- checking the model's questions
def test_good_questions_are_kept_and_tidied():
    q = [nc._clean_question(x) for x in GOOD]
    assert all(q)
    assert q[0]["answer"] == "A. Horizontal velocity"             # letter turned into the full answer
    assert q[0]["options"][0] == "Horizontal velocity"             # "A)" prefix removed


@pytest.mark.parametrize("bad", [
    "not a dict", {}, {"question": "short", "answer": "x"},
    {"type": "short_answer", "question": "A proper question here?", "answer": ""},
    {"type": "multiple_choice", "question": "Which one is right?", "options": ["only one"], "answer": "A"},
    {"type": "multiple_choice", "question": "Which one is right?", "options": ["a", "b", "c"], "answer": "Z"},
])
def test_unusable_questions_are_dropped(bad):
    assert nc._clean_question(bad) is None


def test_a_multiple_choice_answer_written_as_text_is_matched():
    q = nc._clean_question({"type": "multiple_choice", "question": "Which is a vector?",
                            "options": ["Mass", "Velocity", "Time"], "answer": "velocity"})
    assert q["answer"] == "B. Velocity"


def test_missing_marks_get_sensible_defaults():
    q = nc._clean_question({"type": "calculation", "question": "Work out the speed.", "answer": "5 m/s"})
    assert q["marks"] == 3


def test_make_questions_dedupes_trims_and_orders_by_section():
    reply = {"questions": list(reversed(GOOD)) + [GOOD[0], {"junk": 1}]}
    got = nc.make_questions("some notes " * 20, total=3, ask=lambda prompt: __import__("json").dumps(reply))
    assert [q["type"] for q in got] == ["multiple_choice", "short_answer", "calculation"]
    assert len(got) == 3
    assert nc.make_questions("notes", total=5, ask=lambda p: "not json") == []


def test_long_notes_are_sampled_across_the_whole_text():
    seen = []
    nc.make_questions(" ".join(f"w{i}" for i in range(9000)), total=10,
                      ask=lambda prompt: (seen.append(prompt), '{"questions": []}')[1])
    assert len(seen) == 5 and "w0 " in seen[0] and "w8999" in seen[-1]


def test_request_wording():
    assert nc.quiz_request("make me a 5 question multiple choice test on week 4") == (5, "multiple choice")
    assert nc.quiz_request("12 questions short answer") == (12, "short answer")
    assert nc.quiz_request("some calculations please") == (None, "calculations")
    assert nc.quiz_request("quiz physics") == (None, None)


# ---------------------------------------------------------------- the Word file
def test_the_document_has_everything(tmp_path):
    q = [nc._clean_question(x) for x in GOOD]
    path = nc.build_quiz_docx(tmp_path / "t.docx", "Practice test: Physics", "Made today", q)
    text = text_of(path)
    assert "Practice test: Physics" in text and "Total marks: 6" in text
    for heading in ("Multiple choice", "Short answer", "Calculations", "Answer key and worked solutions"):
        assert heading in text
    assert "1.  What stays constant" in text and "A.  Horizontal velocity" in text
    assert "[1 mark]" in text and "[3 marks]" in text
    assert "Answer: A. Horizontal velocity" in text and "d = vt = 10 x 2 = 20 m" in text
    assert "check them with your teacher" in text
    d = docx.Document(str(path))
    assert round(d.sections[0].page_width.cm, 1) == 21.0           # A4
    assert len(d.tables) == 2                                      # answer boxes for the two written questions
    assert "PAGE" in d.sections[0].footer._element.xml            # page numbers


def test_answers_can_go_in_their_own_document(tmp_path):
    q = [nc._clean_question(x) for x in GOOD]
    test = text_of(nc.build_quiz_docx(tmp_path / "t.docx", "T", "s", q, answers_in_file=False))
    key = text_of(nc.build_quiz_docx(tmp_path / "k.docx", "T", "s", q, answers_only=True))
    assert "Answer key" not in test and "d = vt" not in test
    assert "Answer key: T" in key and "Answer: 20 m" in key and "Multiple choice" not in key


# ---------------------------------------------------------------- end to end
@pytest.fixture
def written(model):
    model.json_reply = lambda prompt: {"questions": GOOD}
    return model


def test_make_quiz_from_your_notes(con, written, env, tmp_path):
    add_notes(con, tmp_path)
    path, questions, answers = nc.make_quiz(con, "physics week 4 term 4 projectile motion", count=3)
    assert path.exists() and path.parent == env / "cabinet" / "Practice tests" and answers is None
    assert len(questions) == 3 and "from 1 note" in text_of(path)
    assert "Projectile motion" in "\n".join(written.prompts)         # it really used the notes


def test_make_quiz_separate_answers(con, written, tmp_path):
    add_notes(con, tmp_path)
    path, _, answers = nc.make_quiz(con, "physics", count=3, separate_answers=True)
    assert answers.exists() and "Answer key" not in text_of(path)


def test_quiz_on_your_weak_points(con, written, tmp_path):
    nc.add_weak(con, "physics", "Momentum", "", "p = m v")
    path, _, _ = nc.make_quiz(con, "make a test on what I got wrong in physics", count=3)
    assert "your weak points" in text_of(path)
    assert "Momentum: p = m v" in "\n".join(written.prompts)


def test_helpful_messages_when_there_is_nothing_to_use(con, written):
    with pytest.raises(RuntimeError, match="couldn't find any notes"):
        nc.make_quiz(con, "physics week 4")
    with pytest.raises(RuntimeError, match="review list"):
        nc.make_quiz(con, "test me on what I got wrong")


def test_when_the_model_writes_nothing_usable(con, model, tmp_path):
    add_notes(con, tmp_path)
    model.json_reply = {"questions": [{"junk": True}]}
    with pytest.raises(RuntimeError, match="didn't write any usable questions"):
        nc.make_quiz(con, "physics")


def test_asking_in_plain_words_makes_a_test(con, written, env, tmp_path, capsys):
    add_notes(con, tmp_path)
    nc.cmd_ask(con, argparse.Namespace(question="make me a 3 question test on projectle motion in physics".split()))
    out = capsys.readouterr().out
    assert "Made a practice test with 3 questions (6 marks)" in out
    assert len(list((env / "cabinet" / "Practice tests").glob("*.docx"))) == 1


@pytest.mark.parametrize("q,expected", [
    ("make me a test on week 4 physics", "quiz"), ("quiz me on momentum", "quiz"),
    ("test me on week 4 physics", "quiz"), ("write 10 questions about energy", "quiz"),
    ("give me some practice questions for physics", "quiz"),
    ("summarise week 4 physics", "summary"), ("what did I get wrong in physics", "weak"),
    ("my test is on friday", "summary"),
])
def test_quiz_wording_is_recognised_without_hijacking_other_questions(con, q, expected):
    assert nc.understand(con, q)["intent"] == expected


def test_the_quiz_command_and_chat(con, written, tmp_path, monkeypatch, capsys):
    add_notes(con, tmp_path)
    args = nc.build_parser().parse_args(["quiz", "physics", "-n", "3", "--style", "short answer"])
    args.fn(con, args)
    assert "Made a practice test" in capsys.readouterr().out
    lines = iter(["test me on projectile motion in physics", "quit"])        # not a "test date" command
    monkeypatch.setattr("builtins.input", lambda prompt="": next(lines))
    nc.cmd_chat(con)
    out = capsys.readouterr().out
    assert "Made a practice test" in out and "usage:" not in out


def test_missing_python_docx_is_explained(monkeypatch, tmp_path):
    import sys
    monkeypatch.setitem(sys.modules, "docx", None)
    with pytest.raises(RuntimeError, match="pip install python-docx"):
        nc.build_quiz_docx(tmp_path / "x.docx", "T", "s", [nc._clean_question(GOOD[1])])
