"""Run: python3 -I hermes/tests/test_board_auth.py
Unit tests for the board's login module (password rules, sessions, throttling). Uses a temp folder only."""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "board"))
import board_auth as AU  # noqa: E402

PW = "correct horse battery"


class AuthTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        t = Path(self.tmp.name)
        AU.AUTH_FILE = t / "board-auth.json"
        AU.SESSION_FILE = t / "board-sessions.json"
        AU._cache.update(auth=None, mtime=None)
        AU._fails.clear()
        AU._recent.clear()

    def tearDown(self):
        self.tmp.cleanup()

    def test_password_rules_and_check(self):
        self.assertFalse(AU.configured())
        self.assertFalse(AU.check_password(PW))                         # nothing set: nobody gets in
        for bad in ("short", "a" * 20, "x" * 300):
            with self.assertRaises(ValueError):
                AU.set_password(bad)
        self.assertFalse(AU.configured())
        AU.set_password(PW)
        self.assertTrue(AU.configured())
        self.assertTrue(AU.check_password(PW))
        for wrong in ("", PW + " ", PW.upper(), "x" * 1000, None, 5, ["a"]):
            self.assertFalse(AU.check_password(wrong), wrong)
        raw = AU.AUTH_FILE.read_text()
        self.assertNotIn(PW, raw)
        self.assertEqual(AU.AUTH_FILE.stat().st_mode & 0o777, 0o600)
        # the same password gets a different salt and hash every time
        first = json.loads(raw)
        AU.set_password(PW)
        second = json.loads(AU.AUTH_FILE.read_text())
        self.assertNotEqual((first["salt"], first["hash"]), (second["salt"], second["hash"]))
        self.assertTrue(AU.check_password(PW))

    def test_damaged_file_fails_closed(self):
        AU.set_password(PW)
        AU.AUTH_FILE.write_text("{not json")
        os.utime(AU.AUTH_FILE, ns=(1, 2 * 10 ** 18))
        self.assertFalse(AU.configured())
        self.assertFalse(AU.check_password(PW))

    def test_sessions_expire_renew_and_end(self):
        AU.set_password(PW)
        t0 = 1_800_000_000
        tok = AU.new_session(t0)
        self.assertEqual(AU.SESSION_FILE.stat().st_mode & 0o777, 0o600)
        self.assertNotIn(tok, AU.SESSION_FILE.read_text())               # only a hash is stored
        self.assertEqual(AU.check_session(tok, t0 + 60), (True, False))
        self.assertEqual(AU.check_session(tok, t0 + 86400 + 5), (True, True))    # a day on: pushed out to 30 days again
        self.assertEqual(AU.check_session(tok, t0 + 86400 + 10), (True, False))
        # an unused session lasts 30 days exactly; using it keeps it alive
        idle = AU.new_session(t0)
        self.assertEqual(AU.check_session(idle, t0 + AU.SESSION_SECONDS - 100)[0], True)
        self.assertEqual(AU.check_session(idle, t0 + AU.SESSION_SECONDS + 1)[0], True)   # that use renewed it
        gone = AU.new_session(t0)
        self.assertEqual(AU.check_session(gone, t0 + AU.SESSION_SECONDS + 1), (False, False))
        for junk in ("", None, "nope", "x" * 500, 7):
            self.assertEqual(AU.check_session(junk, t0), (False, False))
        tok2 = AU.new_session(t0)
        AU.end_session(tok2)
        self.assertEqual(AU.check_session(tok2, t0), (False, False))

    def test_changing_the_password_signs_everyone_out(self):
        AU.set_password(PW)
        tok = AU.new_session()
        self.assertTrue(AU.check_session(tok)[0])
        AU.set_password("another long password")
        self.assertFalse(AU.check_session(tok)[0])
        self.assertTrue(AU.check_password("another long password"))
        self.assertFalse(AU.check_password(PW))
        tok = AU.new_session()
        AU.sign_out_all()
        self.assertEqual(AU.session_count(), 0)
        self.assertFalse(AU.check_session(tok)[0])

    def test_session_tied_to_the_password_file(self):
        AU.set_password(PW)
        tok = AU.new_session()
        saved = AU.SESSION_FILE.read_text()
        # even if an old sessions file were put back (a racing writer), a new password ends those sessions
        AU.set_password("another long password")
        AU.SESSION_FILE.write_text(saved)
        self.assertFalse(AU.check_session(tok)[0])
        # and with no password file there is no way in at all
        tok = AU.new_session()
        self.assertTrue(AU.check_session(tok)[0])
        AU.AUTH_FILE.unlink()
        self.assertFalse(AU.check_session(tok)[0])
        with self.assertRaises(RuntimeError):
            AU.new_session()

    def test_files_and_folder_are_private_and_tidy(self):
        AU.AUTH_FILE = Path(self.tmp.name) / "fresh" / "board-auth.json"
        AU.SESSION_FILE = AU.AUTH_FILE.with_name("board-sessions.json")
        AU._cache.update(auth=None, mtime=None)
        AU.set_password(PW)
        AU.new_session()
        self.assertEqual(AU.AUTH_FILE.parent.stat().st_mode & 0o777, 0o700)
        names = sorted(p.name for p in AU.AUTH_FILE.parent.iterdir())
        self.assertEqual(names, ["board-auth.json", "board-auth.json.lock", "board-sessions.json"])   # no stray temp files
        for n in ("board-auth.json", "board-sessions.json", "board-auth.json.lock"):
            self.assertEqual((AU.AUTH_FILE.parent / n).stat().st_mode & 0o777, 0o600, n)

    def test_two_threads_and_the_command_line_take_turns(self):
        import subprocess
        import threading
        AU.set_password(PW)
        toks, errs = [], []

        def worker():
            try:
                for _ in range(8):
                    toks.append(AU.new_session())
            except Exception as e:  # noqa: BLE001
                errs.append(e)
        ts = [threading.Thread(target=worker) for _ in range(4)]
        [t.start() for t in ts]
        # meanwhile the command line writes the same files from another process
        cli = subprocess.run([sys.executable, "-I", str(HERE.parent / "board" / "board_auth.py"), "status"],
                             env={**os.environ, "BOARD_AUTH_FILE": str(AU.AUTH_FILE)}, capture_output=True, text=True)
        [t.join() for t in ts]
        self.assertEqual(errs, [])
        self.assertIn("password: set", cli.stdout)
        # the file is always whole JSON and every token we were given is in it
        for t in toks[-AU.MAX_SESSIONS:]:
            self.assertTrue(AU.check_session(t)[0])

    def test_passwords_are_normalised(self):
        AU.set_password("p\u00e4ssword-long-enough")                 # typed as one character
        self.assertTrue(AU.check_password("pa\u0308ssword-long-enough"))   # typed as letter + combining mark
        self.assertTrue(AU.check_password("\uff50\u00e4ssword-long-enough"))                # a full-width p

    def test_session_cap(self):
        AU.set_password(PW)
        toks = [AU.new_session(1_800_000_000 + i) for i in range(AU.MAX_SESSIONS + 5)]
        self.assertEqual(len(AU._sessions(1_800_000_000)), AU.MAX_SESSIONS)
        self.assertTrue(AU.check_session(toks[-1], 1_800_000_100)[0])      # the newest always survives
        self.assertFalse(AU.check_session(toks[0], 1_800_000_100)[0])

    def test_old_failures_are_forgotten(self):
        t = 1_800_000_000
        ip = "203.0.113.5"
        for _ in range(3):
            AU.record_failure(ip, t)
        AU.record_failure(ip, t + AU.FAIL_FORGET + 5)                       # a typo much later: not "in a row"
        self.assertEqual(AU.wait_for(ip, t + AU.FAIL_FORGET + 5), 0)
        for _ in range(4):
            AU.record_failure(ip, t + AU.FAIL_FORGET + 6)
        self.assertGreater(AU.wait_for(ip, t + AU.FAIL_FORGET + 6), 0)      # but five quick ones still lock

    def test_throttle(self):
        t = 1_800_000_000
        ip = "203.0.113.1"
        for i in range(AU.FAIL_FREE - 1):
            AU.record_failure(ip, t)
            self.assertEqual(AU.wait_for(ip, t), 0)
        AU.record_failure(ip, t)                                            # 5th wrong one: locked for a minute
        self.assertEqual(AU.wait_for(ip, t), 61)
        self.assertEqual(AU.wait_for("203.0.113.2", t), 0)                   # a different address is fine
        self.assertEqual(AU.wait_for(ip, t + 61), 0)
        AU.record_failure(ip, t + 61)                                        # next wrong one: five minutes
        self.assertEqual(AU.wait_for(ip, t + 61), 301)
        for _ in range(6):
            AU.record_failure(ip, t + 400)
        self.assertEqual(AU.wait_for(ip, t + 400), 3601)                     # the wait stops growing at an hour
        AU.record_success(ip)
        self.assertEqual(AU.wait_for(ip, t + 400), 0)

    def test_global_limit(self):
        t = 1_800_000_000
        for i in range(AU.GLOBAL_FAILS):
            AU.record_failure("198.51.100.%d" % i, t + i)
        self.assertGreater(AU.wait_for("192.0.2.1", t + 40), 0)              # everyone waits, even a new address
        self.assertEqual(AU.wait_for("192.0.2.1", t + AU.GLOBAL_WINDOW + 1), 0)


if __name__ == "__main__":
    unittest.main()
