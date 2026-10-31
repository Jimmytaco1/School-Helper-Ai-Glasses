import datetime as dt
import sys
import types

import pytest

import notebook_checker as nc

NOON = dt.datetime(2026, 10, 28, 12)


@pytest.mark.parametrize("url", [
    "https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=5",
    "https://youtu.be/dQw4w9WgXcQ?si=abc",
    "https://www.youtube.com/shorts/dQw4w9WgXcQ",
])
def test_video_id_from_common_links(url):
    assert nc.video_id(url) == "dQw4w9WgXcQ"


def test_other_links_are_rejected():
    assert nc.video_id("https://example.com/watch") is None
    with pytest.raises(ValueError):
        nc.youtube_text("https://example.com/watch")


def fake_transcript_module(monkeypatch, api):
    mod = types.ModuleType("youtube_transcript_api")
    mod.YouTubeTranscriptApi = api
    monkeypatch.setitem(sys.modules, "youtube_transcript_api", mod)


class Snippet:
    def __init__(self, text):
        self.text = text


def test_captions_new_library_version(monkeypatch):
    class Api:
        def fetch(self, vid, languages=None):
            return [Snippet("energy is conserved"), Snippet("in a closed system")]
    fake_transcript_module(monkeypatch, Api)
    assert nc.youtube_text("https://youtu.be/dQw4w9WgXcQ") == ("dQw4w9WgXcQ", "energy is conserved in a closed system")


def test_captions_old_library_version(monkeypatch):
    class Api:
        @staticmethod
        def get_transcript(vid, languages=None):
            return [{"text": "old"}, {"text": "api"}]
    fake_transcript_module(monkeypatch, Api)
    assert nc.youtube_text("https://youtu.be/dQw4w9WgXcQ")[1] == "old api"


def test_falls_back_to_audio_when_there_are_no_captions(monkeypatch):
    class Api:
        def fetch(self, vid, languages=None):
            raise RuntimeError("TranscriptsDisabled")
    fake_transcript_module(monkeypatch, Api)

    class YDL:
        def __init__(self, opts): pass
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def extract_info(self, url, download=True): return {"id": "dQw4w9WgXcQ"}
        def prepare_filename(self, info): return "/tmp/fake.webm"
    yt = types.ModuleType("yt_dlp")
    yt.YoutubeDL = YDL
    monkeypatch.setitem(sys.modules, "yt_dlp", yt)
    monkeypatch.setattr(nc, "transcribe_file", lambda p: "transcribed from audio")
    assert nc.youtube_text("https://youtu.be/dQw4w9WgXcQ")[1] == "transcribed from audio"


def test_process_youtube_files_it_and_shows_the_summary_again_on_a_repeat(con, model, env, monkeypatch, capsys):
    monkeypatch.setattr(nc, "youtube_text", lambda url: ("dQw4w9WgXcQ", "energy is conserved " * 10))
    assert nc.process_youtube(con, "https://youtu.be/dQw4w9WgXcQ", "physics", NOON) is True
    first = capsys.readouterr().out
    assert "LECTURE COMPRESSION REPORT" in first and "term 4 week 4 day 3" in first

    # Running the same link again (say the summary failed last time, or it is a new day)
    # must not fetch it again, must not file a second copy, and must report where it
    # really is stored.
    def must_not_fetch(url):
        raise AssertionError("should not fetch an already stored video")
    monkeypatch.setattr(nc, "youtube_text", must_not_fetch)
    later = dt.datetime(2026, 10, 30, 9, 0)                      # a different day
    assert nc.process_youtube(con, "https://youtu.be/dQw4w9WgXcQ", "physics", later) is True
    again = capsys.readouterr().out
    assert "already stored" in again and "LECTURE COMPRESSION REPORT" in again
    assert "day 3" in again and "day 5" not in again
    assert con.execute("select count(*) from notes").fetchone()[0] == 1
    assert len(list((env / "cabinet").rglob("youtube_*.transcript.txt"))) == 1


def test_bad_links_fail_gracefully(con, capsys):
    assert nc.process_youtube(con, "https://example.com/x", "physics") is False
    assert "Could not get that video" in capsys.readouterr().out
