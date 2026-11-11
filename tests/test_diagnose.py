"""doctor: version mismatch, the verify hint, and the bundle you paste into an issue."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tests.helpers import ROOT

from gearshift import __version__, diagnose, ledger
from gearshift.config import load_config

LAUNCHER = os.path.join(ROOT, "bin", "gearshift.py")


class Diagnose(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp()
        self.state = Path(self.home) / "state"

    def tearDown(self):
        shutil.rmtree(self.home, ignore_errors=True)

    def install(self, *versions):
        d = os.path.join(self.home, ".claude", "plugins")
        os.makedirs(d, exist_ok=True)
        entries = [{"scope": scope, "version": v, "installPath": "/secret/path/%s" % v, "projectPath": "/secret/proj"}
                   for v, scope in versions]
        with open(os.path.join(d, "installed_plugins.json"), "w") as f:
            json.dump({"version": 2, "plugins": {"gearshift@gearshift": entries, "other@x": [{"version": "9.9.9"}]}}, f)

    def lines(self, running="1.5.0", pip=None):
        return "\n".join(diagnose.version_lines({"running": running, "pip": pip,
                                                 "plugins": diagnose.plugin_versions(self.home)}))

    def test_plugin_versions_come_from_claude_codes_own_record(self):
        self.assertEqual(diagnose.plugin_versions(self.home), [])
        self.install(("1.0.0", "project"), ("1.4.1", "user"))
        self.assertEqual(diagnose.plugin_versions(self.home),
                         [{"version": "1.0.0", "scope": "project"}, {"version": "1.4.1", "scope": "user"}])

    def test_an_older_plugin_is_a_warning_with_the_command_that_fixes_it(self):
        # my own machine: plugin 1.0.0, CLI 1.4.1, and nothing said so
        self.install(("1.0.0", "project"))
        text = self.lines(running="1.4.1")
        self.assertIn("[!!] the plugin (1.0.0) is older than this CLI (1.4.1)", text)
        self.assertIn("claude plugin update gearshift@gearshift", text)

    def test_an_older_cli_and_mixed_scopes_and_a_stale_pip_copy(self):
        self.install(("1.10.0", "user"))
        self.assertIn("this CLI (1.9.2) is older than the plugin (1.10.0)", self.lines(running="1.9.2"))
        self.install(("1.4.1", "project"), ("1.5.0", "user"))
        text = self.lines(running="1.5.0", pip="1.4.1")
        self.assertIn("different versions in different scopes", text)
        self.assertIn("a pip-installed gearshift (1.4.1) is not the copy answering now (1.5.0)", text)

    def test_matching_versions_and_no_plugin_are_quiet(self):
        self.assertNotIn("[!!]", self.lines())
        self.assertIn("not installed", self.lines())
        self.install(("1.5.0", "user"))
        self.assertNotIn("[!!]", self.lines(running="1.5.0", pip="1.5.0"))

    def test_verify_hint(self):
        self.assertEqual(diagnose.verify_lines(load_config(None, env={})), [])
        text = "\n".join(diagnose.verify_lines(load_config(None, env={"GEARSHIFT_VERIFY": "pytest -q", "GEARSHIFT_VERIFY_TIMEOUT": "60"})))
        self.assertIn("Run it once by hand", text)
        self.assertIn("pytest -q", text)
        self.assertIn("limit 60s", text)

    def test_the_bundle_carries_no_paths_prompts_or_commands(self):
        self.install(("1.0.0", "project"))
        for e in (
            {"ev": "session", "session": "SECRET-SESSION", "model": "claude-opus-5-5"},
            {"ev": "route", "session": "SECRET-SESSION", "tool_use_id": "toolu_SECRET", "desc": "SECRET description",
             "agent_type": "acme:SECRET-agent", "class": "edit", "action": "pinned", "pinned_model": "sonnet"},
            {"ev": "route", "session": "SECRET-SESSION", "desc": "SECRET two", "agent_type": "my-SECRET-agent",
             "class": "lookup", "action": "filled", "model": "haiku", "effort": "medium"},
            {"ev": "route", "agent_type": "gearshift:scout", "class": "lookup", "action": "pinned",
             "pinned_model": "haiku", "pinned_effort": "medium"},
            {"ev": "done", "status": "completed", "verify": "fail", "escalate_reason": "SECRET reason", "escalated": True},
            {"ev": "check", "verify": "fail", "sent_back": True},
        ):
            ledger.append_event(self.state, e)
        cfg = load_config(None, env={"GEARSHIFT_VERIFY": "python /SECRET/check.py"})
        b = diagnose.build_bundle(cfg, self.state, claude_version="2.1.322", home=self.home)
        text = diagnose.format_bundle(b)
        self.assertNotIn("SECRET", text)
        self.assertNotIn("secret", text)
        self.assertNotIn(self.home, text)
        self.assertNotIn(os.path.expanduser("~"), text)
        self.assertIn("gearshift %s" % __version__, text)
        self.assertIn("plugin=1.0.0(project)", text)
        self.assertIn("verify=set", text)
        self.assertIn("agent=other-plugin", text)
        self.assertIn("agent=custom", text)
        self.assertIn("agent=gearshift:scout ran=haiku@medium", text)
        self.assertIn("checks: fail=2", text)
        self.assertIn("sent_back=True", text)
        self.assertTrue(text.startswith("```") and text.endswith("```"))

    def test_doctor_from_the_command_line(self):
        self.install(("1.0.0", "project"))
        env = dict(os.environ, HOME=self.home, USERPROFILE=self.home, GEARSHIFT_HOME=str(self.state),
                   GEARSHIFT_VERIFY="python -m unittest")
        env.pop("CLAUDE_CONFIG_DIR", None)
        p = subprocess.run([sys.executable, LAUNCHER, "doctor"], capture_output=True, text=True, env=env, cwd=self.home)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn("the plugin (1.0.0) is older than this CLI (%s)" % __version__, p.stdout)
        self.assertIn("Run it once by hand", p.stdout)
        self.assertIn("doctor --bundle", p.stdout)
        p = subprocess.run([sys.executable, LAUNCHER, "doctor", "--bundle"], capture_output=True, text=True, env=env, cwd=self.home)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertTrue(p.stdout.startswith("```"))
        self.assertNotIn(self.home, p.stdout)
        self.assertNotIn("python -m unittest", p.stdout)


if __name__ == "__main__":
    unittest.main()
