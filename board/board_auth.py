#!/usr/bin/env python3
"""Login for the Applications board (standard library only).

One account, password only. The password is never stored: only a salted scrypt hash, in a private file
(`~/.hermes/board-auth.json`, mode 600). A signed-in browser holds a random session token in a cookie; the server keeps
only a SHA-256 of each token (`~/.hermes/board-sessions.json`, mode 600), so a copy of that file can't be used to sign in.
Each session is tied to the current password, so changing the password (or deleting the password file) ends them all.

  python3 board_auth.py set-password     choose or change the password (also signs every browser out)
  python3 board_auth.py sign-out-all     end every session
  python3 board_auth.py status           is a password set, how many sessions

Wrong passwords are slowed down: after 5 in a row from one address the next try must wait 1 minute, then 5, 15 and 60.
Overall, 30 wrong tries in an hour pauses sign-in for everyone until the hour is up. Signed-in browsers are not affected.
These counters live in the server's memory: `systemctl --user restart jobs-board` clears them.
"""
import fcntl
import getpass
import hashlib
import hmac
import json
import os
import secrets
import sys
import tempfile
import threading
import time
import unicodedata
from pathlib import Path

HOME = Path(os.environ.get("HOME") or "~").expanduser()
AUTH_FILE = Path(os.environ.get("BOARD_AUTH_FILE") or HOME / ".hermes" / "board-auth.json")
SESSION_FILE = Path(os.environ.get("BOARD_SESSION_FILE") or AUTH_FILE.with_name("board-sessions.json"))
SESSION_DAYS = 30
SESSION_SECONDS = SESSION_DAYS * 86400
RENEW_AFTER = 86400            # a used session is pushed out to a full 30 days again at most once a day
MAX_SESSIONS = 50
MIN_PASSWORD = 12
MAX_PASSWORD = 256
SCRYPT = {"n": 2 ** 15, "r": 8, "p": 1}
MAXMEM = 128 * 1024 * 1024
FAIL_FREE = 5                  # wrong tries in a row before the waits start
WAITS = [60, 300, 900, 3600]   # seconds, per address
FAIL_FORGET = 3600             # an address that has been quiet this long starts again from zero
GLOBAL_FAILS = 30              # wrong tries per hour, all addresses together
GLOBAL_WINDOW = 3600

LOCK = threading.RLock()       # files
TLOCK = threading.Lock()       # the in-memory throttle tables (never held while touching a file)
_fails = {}                    # address -> [count, locked_until, last_try]
_recent = []                   # timestamps of wrong tries in the last hour (all addresses)
_cache = {"auth": None, "mtime": None}


class _Guard:
    """Threads in this process and other processes (the command line) take turns on the files. Re-entrant."""
    depth = 0
    fd = None

    def __enter__(self):
        LOCK.acquire()
        try:
            if _Guard.depth == 0:
                AUTH_FILE.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                fd = os.open(AUTH_FILE.with_name(AUTH_FILE.name + ".lock"), os.O_RDWR | os.O_CREAT, 0o600)
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX)
                except BaseException:
                    os.close(fd)
                    raise
                _Guard.fd = fd
            _Guard.depth += 1
        except BaseException:
            LOCK.release()
            raise

    def __exit__(self, *exc):
        try:
            _Guard.depth -= 1
            if _Guard.depth == 0:
                fd, _Guard.fd = _Guard.fd, None
                try:
                    fcntl.flock(fd, fcntl.LOCK_UN)
                finally:
                    os.close(fd)
        finally:
            LOCK.release()


def _norm(password):
    return unicodedata.normalize("NFKC", password)    # a phone keyboard and a desktop one type the same password


def _hash(password, salt, params):
    return hashlib.scrypt(_norm(password).encode("utf-8"), salt=salt, n=params["n"], r=params["r"], p=params["p"],
                          maxmem=MAXMEM, dklen=32)


def _write_private(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))   # created mode 600
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _read(path):
    """{} for a missing or damaged file. Any other read error (out of file handles, I/O) is raised, not mistaken for 'empty'."""
    try:
        with open(path) as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except (FileNotFoundError, ValueError):
        return {}


def load_auth():
    """The saved credential, re-read whenever the file changes. {} when no password is set."""
    try:
        st = AUTH_FILE.stat()
    except FileNotFoundError:
        _cache.update(auth={}, mtime=None)
        return {}
    key = (st.st_mtime_ns, st.st_size, st.st_ino)
    if _cache["mtime"] != key:
        _cache.update(auth=_read(AUTH_FILE), mtime=key)
    return _cache["auth"] or {}


def configured():
    a = load_auth()
    return bool(a.get("salt") and a.get("hash"))


def password_problem(pw):
    pw = _norm(pw)
    if len(pw) < MIN_PASSWORD:
        return f"Use at least {MIN_PASSWORD} characters."
    if len(pw) > MAX_PASSWORD:
        return f"Use at most {MAX_PASSWORD} characters."
    if len(set(pw)) < 5:
        return "That password is too repetitive."
    return ""


def set_password(pw):
    bad = password_problem(pw)
    if bad:
        raise ValueError(bad)
    salt = secrets.token_bytes(16)
    digest = _hash(pw, salt, SCRYPT).hex()          # the slow part, before taking the file lock
    with _Guard():
        _write_private(AUTH_FILE, {"v": 2, "salt": salt.hex(), "hash": digest, "params": SCRYPT})
        _write_private(SESSION_FILE, {})        # the new salt already ends old sessions; this just tidies the file


def check_password(pw):
    a = load_auth()
    if not (a.get("salt") and a.get("hash")) or not isinstance(pw, str) or len(pw) > MAX_PASSWORD:
        return False
    try:
        want = bytes.fromhex(a["hash"])
        got = _hash(pw, bytes.fromhex(a["salt"]), a.get("params") or SCRYPT)
    except (ValueError, TypeError, KeyError):
        return False
    return hmac.compare_digest(got, want)


# ------------------------------------------------------------------ throttling
def wait_for(ip, now=None):
    """Seconds this address must still wait before it may try a password (0 = go ahead)."""
    now = now or time.time()
    with TLOCK:
        _recent[:] = [t for t in _recent if now - t < GLOBAL_WINDOW]
        if len(_recent) >= GLOBAL_FAILS:
            return int(GLOBAL_WINDOW - (now - _recent[0])) + 1
        c = _fails.get(ip)
        if c and c[1] > now:
            return int(c[1] - now) + 1
    return 0


def record_failure(ip, now=None):
    now = now or time.time()
    with TLOCK:
        c = _fails.setdefault(ip, [0, 0, now])
        if now - c[2] > FAIL_FORGET and c[1] <= now:     # a typo long after the last one isn't "in a row"
            c[0] = 0
        c[0] += 1
        c[2] = now
        _recent.append(now)
        if c[0] >= FAIL_FREE:
            c[1] = now + WAITS[min(c[0] - FAIL_FREE, len(WAITS) - 1)]
        if len(_fails) > 500:              # keep the table small if an address sweep is running
            for k in [k for k, v in _fails.items() if v[1] < now][:250]:
                _fails.pop(k, None)


def record_success(ip):
    with TLOCK:
        _fails.pop(ip, None)


# ------------------------------------------------------------------ sessions
# sessions file: {sha256(token): [expires_at, salt_of_the_password_it_was_made_under]}
def _sid(token):
    return hashlib.sha256(token.encode()).hexdigest()


def _sessions(now):
    gen = load_auth().get("salt")
    if not gen:
        return {}
    out = {}
    for k, v in _read(SESSION_FILE).items():
        if isinstance(v, list) and len(v) == 2 and isinstance(v[0], (int, float)) and v[0] > now and v[1] == gen:
            out[k] = v
    return out


def new_session(now=None):
    """A fresh token for the cookie (the file keeps only its hash)."""
    now = now or time.time()
    token = secrets.token_urlsafe(32)
    with _Guard():
        s = _sessions(now)
        gen = load_auth().get("salt")
        if not gen:
            raise RuntimeError("no password is set")
        if len(s) >= MAX_SESSIONS:             # drop the ones closest to expiring
            for k in sorted(s, key=lambda k: s[k][0])[:len(s) - MAX_SESSIONS + 1]:
                s.pop(k)
        s[_sid(token)] = [now + SESSION_SECONDS, gen]
        _write_private(SESSION_FILE, s)
    return token


def check_session(token, now=None):
    """(valid, renewed). `renewed` means the cookie should be re-sent with a fresh 30 days."""
    if not token or not isinstance(token, str) or len(token) > 100:
        return False, False
    now = now or time.time()
    key = _sid(token)
    v = _sessions(now).get(key)             # a plain read: the file is always replaced whole, never half-written
    if v is None:
        return False, False
    if v[0] - now >= SESSION_SECONDS - RENEW_AFTER:
        return True, False
    with _Guard():                           # needs a write: take turns, and look again under the lock
        s = _sessions(now)
        v = s.get(key)
        if v is None:
            return False, False
        if v[0] - now < SESSION_SECONDS - RENEW_AFTER:
            s[key] = [now + SESSION_SECONDS, v[1]]
            _write_private(SESSION_FILE, s)
            return True, True
        return True, False


def end_session(token):
    with _Guard():
        s = _sessions(time.time())
        if s.pop(_sid(token or ""), None) is not None:
            _write_private(SESSION_FILE, s)


def sign_out_all():
    with _Guard():
        _write_private(SESSION_FILE, {})


def session_count():
    return len(_sessions(time.time()))


# ------------------------------------------------------------------ command line
def main(argv=None):
    a = list(sys.argv[1:] if argv is None else argv)
    cmd = a[0] if a else ""
    if cmd == "set-password":
        if not sys.stdin.isatty():
            print("Run this in a terminal so the password isn't echoed.", file=sys.stderr)
            return 2
        print(f"Choose a password for the board ({MIN_PASSWORD}+ characters; a few random words works well).")
        p1 = getpass.getpass("New password: ")
        bad = password_problem(p1)
        if bad:
            print(bad, file=sys.stderr)
            return 1
        if getpass.getpass("Again: ") != p1:
            print("Those didn't match. Nothing changed.", file=sys.stderr)
            return 1
        set_password(p1)
        print(f"Saved to {AUTH_FILE} (private). Every browser is signed out; sign in again with the new password.")
        print("If sign-in is paused from too many wrong tries: systemctl --user restart jobs-board")
        return 0
    if cmd == "sign-out-all":
        sign_out_all()
        print("Every browser is signed out.")
        return 0
    if cmd == "status":
        print("password:", "set" if configured() else "NOT SET (run set-password)")
        print("signed-in browsers:", session_count())
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main())
