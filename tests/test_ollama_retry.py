"""Ollama sometimes answers 'busy' (503). These tests use a fake ollama module."""
import sys
import types

import pytest

import notebook_checker as nc


class ResponseError(Exception):
    def __init__(self, msg, code):
        super().__init__(msg)
        self.status_code = code


@pytest.fixture
def fake_ollama(monkeypatch):
    mod = types.ModuleType("ollama")
    mod.ResponseError = ResponseError
    mod.seen_ctx = []
    monkeypatch.setitem(sys.modules, "ollama", mod)
    monkeypatch.setattr(nc.time, "sleep", lambda s: None)
    return mod


def test_busy_server_is_retried_with_less_memory(fake_ollama):
    attempts = []

    def chat(model, messages, keep_alive=None, options=None, **kw):
        attempts.append(options["num_ctx"])
        if len(attempts) < 3:
            raise ResponseError("busy", 503)
        return {"message": {"content": "worked"}}
    fake_ollama.chat = chat
    assert nc.chat_llm([{"role": "user", "content": "hi"}]) == "worked"
    assert attempts == [4096, 4096, 2048]


def test_it_gives_up_politely(fake_ollama):
    def chat(*a, **k):
        raise ResponseError("busy", 503)
    fake_ollama.chat = chat
    with pytest.raises(nc.OllamaProblem) as err:
        nc.chat_llm([])
    assert "Close other programs" in str(err.value)


def test_missing_model_says_how_to_get_it(fake_ollama):
    def chat(*a, **k):
        raise ResponseError("not found", 404)
    fake_ollama.chat = chat
    with pytest.raises(nc.OllamaProblem) as err:
        nc.chat_llm([])
    assert "ollama pull" in str(err.value)


def test_ollama_not_running_is_explained(fake_ollama):
    class ConnectError(Exception):
        pass

    def chat(*a, **k):
        raise ConnectError("All connection attempts failed")
    fake_ollama.chat = chat
    with pytest.raises(nc.OllamaProblem) as err:
        nc.chat_llm([])
    assert "Is it running" in str(err.value)


def test_json_mode_is_requested_when_asked(fake_ollama):
    seen = {}

    def chat(model, messages, keep_alive=None, options=None, **kw):
        seen.update(kw)
        return {"message": {"content": "{}"}}
    fake_ollama.chat = chat
    nc.chat_llm([], as_json=True)
    assert seen == {"format": "json"}


def test_local_calls_bypass_any_proxy():
    import os
    assert "127.0.0.1" in os.environ["NO_PROXY"] and "localhost" in os.environ["NO_PROXY"]


def test_the_model_is_unloaded_from_memory_soon_after_use(fake_ollama, monkeypatch):
    seen = {}

    def chat(model, messages, keep_alive=None, options=None, **kw):
        seen["keep_alive"] = keep_alive
        return {"message": {"content": "ok"}}
    fake_ollama.chat = chat
    nc.chat_llm([])
    assert seen["keep_alive"] == "5m"
    monkeypatch.setattr(nc, "KEEP_ALIVE", "30m")
    nc.chat_llm([])
    assert seen["keep_alive"] == "30m"
