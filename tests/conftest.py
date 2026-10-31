"""Shared test setup. A fake model stands in for Ollama, so the tests are fast,
need no GPU, and give the same answer every time."""
import datetime as dt
import json
import sys
from pathlib import Path

import pytest

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))

import notebook_checker as nc  # noqa: E402

FIXED_TODAY = dt.date(2026, 10, 28)       # a Wednesday: term 4, week 4, day 3


class FakeModel:
    def __init__(self):
        self.prompts = []
        self.json_reply = {"summary": "a summary", "feedback": "some feedback", "mistakes": []}
        self.page_text = "Date: 21/10/2026\nv = u + a t"
        self.subject_guess = "Physics."

    def llm(self, prompt, images=None, as_json=False, ctx=None):
        self.prompts.append(prompt)
        if as_json:
            reply = self.json_reply(prompt) if callable(self.json_reply) else self.json_reply
            return json.dumps(reply)
        if images:
            return self.page_text
        if "Which school subject" in prompt:
            return self.subject_guess
        return "- a bullet point summary"


@pytest.fixture
def env(tmp_path, monkeypatch):
    """An empty notebook in a temporary folder, with 'today' fixed."""
    cabinet = tmp_path / "cabinet"
    monkeypatch.setattr(nc, "ROOT", cabinet)
    monkeypatch.setattr(nc, "DB_PATH", cabinet / "notebook.db")
    monkeypatch.setattr(nc, "today", lambda: FIXED_TODAY)
    return tmp_path


@pytest.fixture
def model(env, monkeypatch):
    m = FakeModel()
    monkeypatch.setattr(nc, "llm", m.llm)
    return m


@pytest.fixture
def con(env):
    c = nc.connect()
    yield c
    c.close()


def make_image(path, exif_date=None):
    from PIL import Image
    img = Image.new("RGB", (40, 40), "white")
    if exif_date:
        ex = Image.Exif()
        ex[306] = exif_date
        img.save(path, exif=ex)
    else:
        img.save(path)
    return path
