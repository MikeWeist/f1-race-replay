"""
Where the saved F1 TV token lives: F1TV_TOKEN in this project's .env, else ~/.f1tv_token.

Both are outside git (.env is in .gitignore). raw_token() returns the text as saved; callers parse it.

F1's archive (livetiming.formula1.com/static/) answers 401 without a login, so archive_headers()
gives the Authorization header it accepts, and authorize_requests() adds it to every `requests`
call for that archive (including the ones inside livef1). Standard library only, so it ships in
the shared zip, where there is no token and nothing is added.
"""
import json
import os
from urllib.parse import unquote

ARCHIVE = "https://livetiming.formula1.com/static/"

TOKEN_FILE = os.path.join(os.path.expanduser("~"), ".f1tv_token")
ENV_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
ENV_KEY = "F1TV_TOKEN"
ENV_HEADER = ("# Local secrets. Git-ignored: never commit this file.\n"
              "# Paste the whole value of formula1.com's login-session cookie after the = (one line, no quotes).\n")


def env_value(name, path=ENV_FILE):
    """A KEY=value from a .env file, or None when the file or key is missing or empty."""
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        for line in f:
            key, sep, value = line.strip().partition("=")
            if sep and key.strip() == name:
                return value.strip().strip("'\"") or None
    return None


def raw_token():
    """The saved token text, or None."""
    token = env_value(ENV_KEY)
    if token:
        return token
    if os.path.exists(TOKEN_FILE):
        with open(TOKEN_FILE, encoding="utf-8") as f:
            return f.read()
    return None


def bare_token(raw):
    """The JWT inside what was saved (the bare token or the whole login-session cookie), or None."""
    raw = (raw or "").strip()
    try:
        if raw.startswith("%7B") or raw.startswith("{"):
            raw = json.loads(unquote(raw))["data"]["subscriptionToken"]
    except (ValueError, KeyError, TypeError):
        return None
    return raw if len(raw.split(".")) == 3 else None


def archive_headers(url=ARCHIVE):
    """{'Authorization': 'Bearer …'} for an archive URL when a token is saved, else {}."""
    if not str(url).startswith(ARCHIVE):
        return {}
    token = bare_token(raw_token())
    return {"Authorization": f"Bearer {token}"} if token else {}


def authorize_requests():
    """Send the saved token with every `requests` call to the archive. Returns whether one is saved."""
    import requests

    headers = archive_headers()
    if headers and not getattr(requests.Session.request, "_archive_auth", False):
        plain = requests.Session.request

        def request(self, method, url, *args, **kwargs):
            if str(url).startswith(ARCHIVE):
                kwargs["headers"] = {**(kwargs.get("headers") or {}), **headers}
            return plain(self, method, url, *args, **kwargs)

        request._archive_auth = True
        requests.Session.request = request
    return bool(headers)


def save_token(token, path=ENV_FILE):
    """Write F1TV_TOKEN into the .env, replacing an earlier value and keeping every other line."""
    lines, found = [], False
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            for line in f:
                if line.strip().partition("=")[0].strip() == ENV_KEY:
                    if not found:
                        lines.append(f"{ENV_KEY}={token}\n")
                    found = True
                else:
                    lines.append(line if line.endswith("\n") else line + "\n")
    else:
        lines.append(ENV_HEADER)
    if not found:
        lines.append(f"{ENV_KEY}={token}\n")
    with open(path, "w", encoding="utf-8") as f:
        f.writelines(lines)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
