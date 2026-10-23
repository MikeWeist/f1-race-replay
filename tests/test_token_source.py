"""token_source: reading and writing F1TV_TOKEN in a .env, without touching the real one."""
import json
from urllib.parse import quote

import token_source
from token_source import ENV_KEY, bare_token, env_value, save_token


def test_save_creates_env_with_header(tmp_path):
    env = tmp_path / ".env"
    save_token("a.b.c", str(env))
    text = env.read_text(encoding="utf-8")
    assert text.startswith("# Local secrets.")
    assert env_value(ENV_KEY, str(env)) == "a.b.c"


def test_save_replaces_value_and_keeps_other_lines(tmp_path):
    env = tmp_path / ".env"
    env.write_text("# note\nOTHER=1\nF1TV_TOKEN=old\nF1TV_TOKEN=dupe\nLAST=2", encoding="utf-8")
    save_token("new.tok.en", str(env))
    assert env.read_text(encoding="utf-8") == "# note\nOTHER=1\nF1TV_TOKEN=new.tok.en\nLAST=2\n"


def test_empty_or_missing_value_is_none(tmp_path):
    env = tmp_path / ".env"
    assert env_value(ENV_KEY, str(env)) is None
    env.write_text("F1TV_TOKEN=\n", encoding="utf-8")
    assert env_value(ENV_KEY, str(env)) is None


def test_bare_token_unwraps_the_cookie():
    cookie = quote(json.dumps({"data": {"subscriptionToken": "x.y.z"}}))
    assert bare_token(cookie) == "x.y.z"
    assert bare_token("x.y.z\n") == "x.y.z"
    assert bare_token("cut-off") is None


def test_archive_headers_only_for_the_archive(monkeypatch):
    monkeypatch.setattr(token_source, "raw_token", lambda: "x.y.z")
    assert token_source.archive_headers(token_source.ARCHIVE + "2026/Index.json") == {"Authorization": "Bearer x.y.z"}
    assert token_source.archive_headers("https://example.com/") == {}
    monkeypatch.setattr(token_source, "raw_token", lambda: None)
    assert token_source.archive_headers() == {}
