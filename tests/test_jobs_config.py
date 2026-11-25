"""Run: python3 -I tests/test_jobs_config.py   (settings loader, example-config drift, demo build guards)."""
import copy
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import jobs_config as C  # noqa: E402


class Env:
    """Point the loader at a temp file for one test."""
    def __init__(self, content=None):
        self.content = content

    def __enter__(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "c.json"
        if self.content is not None:
            self.path.write_text(self.content if isinstance(self.content, str) else json.dumps(self.content))
        self.saved = {k: os.environ.get(k) for k in ("JOBS_AGENT_CONFIG", "BOARD_ALLOWED_HOSTS", "BOARD_INTAKE_JOB_ID", "JOBS_EXPORT_DIR")}
        for k in ("BOARD_ALLOWED_HOSTS", "BOARD_INTAKE_JOB_ID", "JOBS_EXPORT_DIR"):
            os.environ.pop(k, None)
        os.environ["JOBS_AGENT_CONFIG"] = str(self.path)
        C._cache["key"], C._cache["value"] = None, None
        self.err = io.StringIO()
        self._r = redirect_stderr(self.err)
        self._r.__enter__()
        return self

    def __exit__(self, *a):
        self._r.__exit__(*a)
        for k, v in self.saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        C._cache["key"], C._cache["value"] = None, None
        self.tmp.cleanup()


class ConfigTests(unittest.TestCase):
    def test_missing_file_gives_placeholders(self):
        with Env():
            self.assertTrue(C.is_placeholder())
            self.assertEqual(C.name_slug(), "Your-Name")

    def test_nested_resume_block_merges_key_by_key(self):
        with Env({"name": "A B", "email": "a@b.example", "resume": {"years_phrase": "9+ years"}}) as e:
            r = C.resume()
            self.assertEqual(r["years_phrase"], "9+ years")
            self.assertIn("software_roles", r)
            self.assertFalse(C.is_placeholder())
            self.assertEqual(e.err.getvalue(), "")

    def test_broken_json_warns_and_falls_back(self):
        with Env("{not json") as e:
            self.assertTrue(C.is_placeholder())
            self.assertIn("could not read", e.err.getvalue())

    def test_wrong_types_fall_back_with_warning(self):
        bad = {"name": None, "allowed_hosts": "board.example", "export_dir": None, "resume": None}
        with Env(bad) as e:
            self.assertEqual(C.get("name"), C.DEFAULTS["name"])
            self.assertEqual(C.allowed_hosts(), set(C.DEFAULTS["allowed_hosts"]) | set())
            self.assertIsInstance(C.resume(), dict)
            self.assertIn("ignoring", e.err.getvalue())

    def test_bad_regex_and_pairs_are_dropped(self):
        with Env({"resume": {"banned_claims": [["(", "broken"], ["ok\\b", "fine"]], "degree_rewrite": "x", "years_phrase": " "}}) as e:
            r = C.resume()
            self.assertEqual(r["banned_claims"], [["ok\\b", "fine"]])
            self.assertEqual(r["degree_rewrite"], C.DEFAULTS["resume"]["degree_rewrite"])
            self.assertTrue(r["years_phrase"].strip())

    def test_edits_are_picked_up(self):
        with Env({"name": "First Person", "email": "a@b.example"}) as e:
            self.assertEqual(C.name_slug(), "First-Person")
            st = e.path.stat()
            e.path.write_text(json.dumps({"name": "Second Person Longer", "email": "a@b.example"}))
            self.assertEqual(C.name_slug(), "Second-Person-Longer")

    def test_name_slug_keeps_accented_letters(self):
        with Env({"name": "José Álvarez", "email": "a@b.example"}):
            self.assertEqual(C.name_slug(), "Jose-Alvarez")

    def test_env_beats_file_and_empty_env_is_ignored(self):
        with Env({"allowed_hosts": ["a.example"], "intake_job_id": " fromfile "}):
            self.assertEqual(C.intake_job_id(), "fromfile")
            self.assertIn("a.example", C.allowed_hosts())
            os.environ["BOARD_ALLOWED_HOSTS"] = ""
            self.assertIn("a.example", C.allowed_hosts())
            os.environ["BOARD_ALLOWED_HOSTS"] = "b.example"
            self.assertIn("b.example", C.allowed_hosts())
            self.assertNotIn("a.example", C.allowed_hosts())
            os.environ["BOARD_INTAKE_JOB_ID"] = "fromenv"
            self.assertEqual(C.intake_job_id(), "fromenv")

    def test_example_config_matches_defaults(self):
        ex = json.loads((ROOT / "config" / "job-agent.example.json").read_text())
        keys = lambda d: {k for k in d if not k.startswith("_")}
        self.assertEqual(keys(ex), keys(C.DEFAULTS))
        self.assertEqual(keys(ex["resume"]), keys(C.DEFAULTS["resume"]))
        with Env(ex) as e:
            self.assertTrue(C.is_placeholder())      # shipped example must not look configured
            self.assertEqual(e.err.getvalue(), "")   # and must validate cleanly

    def test_blank_values_fall_back_to_placeholders(self):
        with Env({"name": "  ", "email": "", "export_dir": "", "resume": {"years_phrase": ""}}) as e:
            self.assertTrue(C.is_placeholder())
            self.assertTrue(C.get("export_dir").strip())

    def test_resume_request_refuses_until_configured(self):
        with Env({}) as e:
            env = dict(os.environ, JOBS_AGENT_CONFIG=str(e.path))
            r = subprocess.run([sys.executable, "-I", str(ROOT / "scripts" / "resume_request.py"), "ABCD"],
                               capture_output=True, text=True, env=env)
            self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
            self.assertIn("set your name and email first", r.stdout)

    def test_test_fixture_is_valid_and_configured(self):
        with Env(json.loads((ROOT / "tests" / "fixtures" / "job-agent.test.json").read_text())) as e:
            self.assertFalse(C.is_placeholder())
            self.assertEqual(e.err.getvalue(), "")


class DemoBuildTests(unittest.TestCase):
    def build(self, out):
        return subprocess.run([sys.executable, "-I", str(ROOT / "demo" / "build.py"), str(out)], capture_output=True, text=True)

    def test_builds_and_has_no_root_absolute_links(self):
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "site"
            r = self.build(out)
            self.assertEqual(r.returncode, 0, r.stderr)
            html = (out / "index.html").read_text()
            self.assertIn("__DEMO__", html)
            self.assertNotIn("manifest.webmanifest", html)
            self.assertFalse((out / "sw.js").exists())
            self.assertTrue((out / ".nojekyll").exists())
            self.assertNotIn("<!--", html.split('id="demo-fixtures"')[1].split("</script>")[0])
            self.assertEqual(self.build(out).returncode, 0)    # rebuild over its own output

    def test_refuses_dangerous_targets(self):
        for bad in (ROOT, ROOT / "scripts", ROOT / ".git", ROOT / "demo", ROOT.parent,
                    ROOT / "scripts" / "docs", ROOT / "board" / "static" / "docs"):
            r = self.build(bad)
            self.assertNotEqual(r.returncode, 0, bad)
            self.assertTrue((ROOT / "scripts" / "jobs_config.py").exists())
        with tempfile.TemporaryDirectory() as d:        # a gh-pages style checkout is never wiped
            (Path(d) / ".nojekyll").write_text("")
            (Path(d) / ".git").mkdir()
            self.assertNotEqual(self.build(d).returncode, 0)
            self.assertTrue((Path(d) / ".git").exists())
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "keep.txt").write_text("x")
            r = self.build(d)
            self.assertNotEqual(r.returncode, 0)
            self.assertTrue((Path(d) / "keep.txt").exists())

    def test_fixtures_have_no_personal_markers(self):
        text = (ROOT / "demo" / "fixtures.json").read_text().lower()
        for marker in ("@gmail", "/home/", ".ca/"):
            self.assertNotIn(marker, text)


if __name__ == "__main__":
    unittest.main()
