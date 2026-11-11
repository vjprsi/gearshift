import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

from tests.helpers import ROOT

from gearshift.config import load_config
from gearshift.context import agent_footprint, footprint_lines, main_context_tokens

LAUNCHER = os.path.join(ROOT, "bin", "gearshift.py")
EDIT = {"subagent_type": "general-purpose", "description": "Fix typo",
        "prompt": "Fix the typo in the error message in login.py."}
LOOKUP = {"subagent_type": "general-purpose", "description": "Find API endpoints",
          "prompt": "Find all API endpoints defined in this repo and list the handler files."}


def write_agent(folder, name, desc, model=None):
    os.makedirs(folder, exist_ok=True)
    with open(os.path.join(folder, name + ".md"), "w") as f:
        f.write("---\nname: %s\ndescription: %s\n%s---\nbody\n" % (name, desc, "model: %s\n" % model if model else ""))


class Footprint(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.home, ignore_errors=True)

    def test_counts_agents_from_you_and_from_plugins(self):
        c = os.path.join(self.home, ".claude")
        write_agent(os.path.join(c, "agents"), "mine", "x" * 400)
        for i in range(60):
            write_agent(os.path.join(c, "plugins", "cache", "market", "bigpack", "1.0.0", "agents"), "a%d" % i, "y" * 1200)
            write_agent(os.path.join(c, "plugins", "cache", "market", "bigpack", "0.9.0", "agents"), "a%d" % i, "y" * 1200)
        write_agent(os.path.join(c, "plugins", "cache", "market", "bigpack", "1.0.0", "skills", "s"), "SKILL", "not an agent")
        fp = agent_footprint(home=self.home)
        self.assertEqual(fp["agents"], 61, "two cached versions of a plugin count once; skills are not agents")
        self.assertEqual(fp["sources"][0]["source"], "bigpack")
        self.assertTrue(17000 < fp["tokens"] < 20000, fp["tokens"])
        text = "\n".join(footprint_lines(fp))
        self.assertIn("[!!] 61 agents installed", text)
        self.assertIn("bigpack", text)

    def test_small_install_is_fine_and_empty_is_silent(self):
        self.assertEqual(footprint_lines(agent_footprint(home=self.home)), [])
        write_agent(os.path.join(self.home, ".claude", "agents"), "mine", "short")
        self.assertIn("[ok] 1 agents installed", footprint_lines(agent_footprint(home=self.home))[0])

    def test_doctor_reports_it_without_failing(self):
        for i in range(80):
            write_agent(os.path.join(self.home, ".claude", "agents"), "a%d" % i, "z" * 1000)
        env = dict(os.environ, HOME=self.home, USERPROFILE=self.home, GEARSHIFT_HOME=os.path.join(self.home, "st"))
        p = subprocess.run([sys.executable, LAUNCHER, "doctor"], capture_output=True, text=True, env=env)
        self.assertIn("80 agents installed", p.stdout)
        self.assertIn("all good", p.stdout)

    def test_main_context_from_transcript(self):
        path = os.path.join(self.home, "t.jsonl")
        with open(path, "w") as f:
            f.write(json.dumps({"type": "assistant", "message": {"usage": {"input_tokens": 5, "cache_read_input_tokens": 1000}}}) + "\n")
            f.write(json.dumps({"type": "assistant", "message": {"usage": {
                "input_tokens": 300, "cache_read_input_tokens": 170000, "cache_creation_input_tokens": 8000, "output_tokens": 900}}}) + "\n")
            f.write(json.dumps({"type": "assistant", "isSidechain": True, "message": {"usage": {"input_tokens": 9}}}) + "\n")
            f.write(json.dumps({"type": "user", "message": {"content": "usage"}}) + "\n")
        self.assertEqual(main_context_tokens(path), 178300)
        self.assertIsNone(main_context_tokens(os.path.join(self.home, "missing.jsonl")))
        self.assertIsNone(main_context_tokens(None))

    def test_statusline_shows_session_cost_and_context(self):
        path = os.path.join(self.home, "t.jsonl")
        with open(path, "w") as f:
            f.write(json.dumps({"type": "assistant", "message": {"usage": {"input_tokens": 300, "cache_read_input_tokens": 178000}}}) + "\n")
        env = dict(os.environ, HOME=self.home, USERPROFILE=self.home, GEARSHIFT_HOME=os.path.join(self.home, "st"),
                   PYTHONPATH=os.path.join(ROOT, "src"))
        env.pop("GEARSHIFT_MODE", None)

        def line(payload):
            return subprocess.run([sys.executable, "-m", "gearshift", "statusline"], input=json.dumps(payload),
                                  capture_output=True, text=True, env=env).stdout.strip()
        self.assertEqual(line({"session_id": "s1", "transcript_path": path, "cost": {"total_cost_usd": 7.371}}),
                         "gearshift fill | no subagents yet | session $7.37 | ctx 178k: /clear between tasks")
        with open(path, "w") as f:
            f.write(json.dumps({"type": "assistant", "message": {"usage": {"input_tokens": 300, "cache_read_input_tokens": 40000}}}) + "\n")
        self.assertEqual(line({"session_id": "s1", "transcript_path": path}), "gearshift fill | no subagents yet | ctx 40k")
        self.assertEqual(line({"session_id": "s1", "transcript_path": 7, "cost": "x"}), "gearshift fill | no subagents yet")


class Verify(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.env = dict(os.environ)
        self.env.update({"HOME": self.tmp, "USERPROFILE": self.tmp, "GEARSHIFT_HOME": os.path.join(self.tmp, "state"),
                         "XDG_CONFIG_HOME": os.path.join(self.tmp, ".config")})
        for k in list(self.env):
            if k.startswith(("CLAUDE_PLUGIN_OPTION_", "GEARSHIFT_MODE", "GEARSHIFT_PROBE", "GEARSHIFT_VERIFY",
                             "CLAUDE_PROJECT_DIR", "ANTHROPIC_API_KEY", "GEARSHIFT_API_KEY", "GEARSHIFT_JUDGE")):
                self.env.pop(k)
        self.transcript = os.path.join(self.tmp, "main.jsonl")
        with open(self.transcript, "w") as f:
            f.write(json.dumps({"type": "assistant", "version": "2.1.322",
                                "message": {"id": "m1", "model": "claude-opus-5-5",
                                            "usage": {"input_tokens": 10, "output_tokens": 5}}}) + "\n")
        self.marker = os.path.join(self.tmp, "ran.txt")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def check(self, code):
        # a check that leaves a marker (so we know it ran) and exits with `code`
        return '"%s" -c "open(r\'%s\', \'a\').write(\'x\'); print(\'2 tests failed\'); raise SystemExit(%d)"' % (
            sys.executable, self.marker, code)

    def hook(self, event, payload, **env):
        e = dict(self.env)
        e.update(env)
        return subprocess.run([sys.executable, LAUNCHER, "hook", event], input=json.dumps(payload),
                              capture_output=True, text=True, env=e, cwd=self.tmp, timeout=60)

    def run_task(self, tool_input, answer, tid="tu1", **env):
        base = {"session_id": "s1", "transcript_path": self.transcript, "cwd": self.tmp, "tool_name": "Agent",
                "tool_use_id": tid}
        self.hook("pre-agent", dict(base, tool_input=tool_input), **env)
        return self.hook("post-agent", dict(base, tool_input={}, tool_response={
            "status": "completed", "agentId": "a" + tid, "content": [{"type": "text", "text": answer}]}), **env)

    def events(self, kind):
        with open(os.path.join(self.tmp, "state", "events.jsonl")) as f:
            return [e for e in map(json.loads, f) if e["ev"] == kind]

    def test_confident_but_wrong_is_caught(self):
        p = self.run_task(EDIT, "Done. Fixed the typo, everything works.", GEARSHIFT_VERIFY=self.check(1))
        self.assertEqual(p.returncode, 0, p.stderr)
        ctx = json.loads(p.stdout)["hookSpecificOutput"]["additionalContext"]
        self.assertIn("reported the work done, but your check failed", ctx)
        self.assertIn("2 tests failed", ctx)
        self.assertIn("Do not build on this result", ctx)
        self.assertIn("model=sonnet, effort=high", ctx)
        self.assertEqual(self.events("done")[-1]["verify"], "fail")
        # and the same task sent again starts one step up
        base = {"session_id": "s1", "transcript_path": self.transcript, "cwd": self.tmp, "tool_name": "Agent"}
        p = self.hook("pre-agent", dict(base, tool_use_id="tu2", tool_input=EDIT), GEARSHIFT_VERIFY=self.check(1))
        up = json.loads(p.stdout)["hookSpecificOutput"]["updatedInput"]
        self.assertEqual((up["model"], up["effort"]), ("sonnet", "high"))

    def test_passing_check_is_quiet(self):
        p = self.run_task(EDIT, "Done.", GEARSHIFT_VERIFY=self.check(0))
        self.assertEqual(p.stdout, "")
        self.assertEqual(self.events("done")[-1]["verify"], "pass")
        self.run_task(EDIT, "Done again.", tid="tu5", GEARSHIFT_VERIFY=self.check(1))
        rep = subprocess.run([sys.executable, LAUNCHER, "report"], capture_output=True, text=True, env=self.env).stdout
        self.assertIn("your check after code changes: 1 passed, 1 failed", rep)

    def test_read_only_work_is_not_checked(self):
        p = self.run_task(LOOKUP, "Found 14 endpoints.", GEARSHIFT_VERIFY=self.check(1))
        self.assertEqual(p.stdout, "")
        self.assertFalse(os.path.exists(self.marker))
        self.assertNotIn("verify", self.events("done")[-1])

    def test_off_by_default_and_not_in_shadow_mode(self):
        self.run_task(EDIT, "Done.")
        self.assertNotIn("verify", self.events("done")[-1])
        p = self.run_task(EDIT, "Done.", tid="tu9", GEARSHIFT_VERIFY=self.check(1), GEARSHIFT_MODE="shadow")
        self.assertEqual(p.stdout, "")
        self.assertFalse(os.path.exists(self.marker))

    def test_a_cloned_repo_cannot_make_the_hook_run_a_command(self):
        with open(os.path.join(self.tmp, ".gearshift.json"), "w") as f:
            json.dump({"verify": self.check(1)}, f)
        self.assertEqual(load_config(self.tmp, env={})["verify"], "")
        self.run_task(EDIT, "Done.", CLAUDE_PROJECT_DIR=self.tmp)
        self.assertFalse(os.path.exists(self.marker))

    def test_a_check_that_cannot_run_is_not_a_failure(self):
        p = self.run_task(EDIT, "Done.", GEARSHIFT_VERIFY="definitely-not-a-command-xyz")
        # the shell reports 127: that IS a failed check, and the user should hear about a broken command
        self.assertIn("check failed", p.stdout)
        from unittest import mock
        from gearshift import claude_code
        with mock.patch.object(claude_code, "VERIFY_TIMEOUT", 1):
            ok, _ = claude_code._run_verify('"%s" -c "import time; time.sleep(5)"' % sys.executable, self.tmp)
        self.assertIsNone(ok, "a timeout means unknown, never a failure")

    def background(self, tool_input, final_text, tid="bg1", agent="agent-bg1", stop_hook_active=False, **env):
        """What Claude Code really does with a background agent: PostToolUse at launch, SubagentStop at the end."""
        base = {"session_id": "s1", "transcript_path": self.transcript, "cwd": self.tmp, "tool_name": "Agent",
                "tool_use_id": tid}
        self.hook("pre-agent", dict(base, tool_input=tool_input), **env)
        launch = self.hook("post-agent", dict(base, tool_input={}, tool_response={
            "status": "async_launched", "agentId": agent, "totalDurationMs": None}), **env)
        sub = os.path.join(self.tmp, agent + ".jsonl")
        with open(sub, "w") as f:
            f.write(json.dumps({"type": "assistant", "message": {"id": "r1", "model": "claude-sonnet-5-5",
                                "usage": {"input_tokens": 100, "output_tokens": 50},
                                "content": [{"type": "text", "text": "Looking at the code."}]}}) + "\n")
            f.write(json.dumps({"type": "assistant", "message": {"id": "r2", "model": "claude-sonnet-5-5",
                                "usage": {"input_tokens": 100, "output_tokens": 50},
                                "content": [{"type": "text", "text": final_text}]}}) + "\n")
        stop = self.hook("subagent-stop", {"session_id": "s1", "agent_id": agent, "agent_type": "general-purpose",
                                           "agent_transcript_path": sub, "cwd": self.tmp,
                                           "stop_hook_active": stop_hook_active}, GEARSHIFT_STOP_DELAY="0", **env)
        return launch, stop

    def test_a_background_launch_is_not_a_result(self):
        launch, stop = self.background(LOOKUP, "Found 14 endpoints.", GEARSHIFT_VERIFY=self.check(1))
        self.assertEqual((launch.stdout, stop.stdout), ("", ""))
        self.assertFalse(os.path.exists(self.marker), "nothing to check at launch, and a lookup is never checked")
        self.assertEqual(len(self.events("launched")), 1)
        done = self.events("done")
        self.assertEqual(len(done), 1)
        self.assertEqual((done[0]["tool_use_id"], done[0]["background"], done[0]["status"]), ("bg1", True, "completed"))

    def test_background_escalation_is_seen_at_the_real_end(self):
        self.background(LOOKUP, "I tried.\n\nESCALATE: the routes are generated at runtime")
        done = self.events("done")[-1]
        self.assertTrue(done["escalated"])
        base = {"session_id": "s1", "transcript_path": self.transcript, "cwd": self.tmp, "tool_name": "Agent"}
        p = self.hook("pre-agent", dict(base, tool_use_id="bg2", tool_input=LOOKUP))
        up = json.loads(p.stdout)["hookSpecificOutput"]["updatedInput"]
        self.assertEqual((up["model"], up["effort"]), ("haiku", "high"))

    def test_background_code_change_with_failing_check_is_sent_back_once(self):
        launch, stop = self.background(EDIT, "Done, all good.", GEARSHIFT_VERIFY=self.check(1))
        self.assertEqual(launch.stdout, "")
        out = json.loads(stop.stdout)
        self.assertEqual(out["decision"], "block")
        self.assertIn("2 tests failed", out["reason"])
        self.assertIn("ESCALATE:", out["reason"])
        self.assertEqual(self.events("done"), [], "it has not finished yet, so no result is recorded")
        # second time round it still fails: let it go, record the failure, start higher next time
        _, stop = self.background(EDIT, "Still done.", stop_hook_active=True, GEARSHIFT_VERIFY=self.check(1))
        self.assertEqual(stop.stdout, "")
        self.assertEqual(self.events("done")[-1]["verify"], "fail")

    def test_background_code_change_with_passing_check(self):
        _, stop = self.background(EDIT, "Done.", GEARSHIFT_VERIFY=self.check(0))
        self.assertEqual(stop.stdout, "")
        self.assertEqual(self.events("done")[-1]["verify"], "pass")

    def test_old_ledgers_do_not_count_launches_as_finished(self):
        from pathlib import Path
        from gearshift import ledger
        from gearshift.outcomes import collect_runs
        state = Path(self.tmp) / "old"
        for i in range(19):
            ledger.append_event(state, {"ev": "route", "tool_use_id": "o%d" % i, "class": "lookup", "action": "pinned",
                                        "pinned_model": "haiku", "session": "s"})
            ledger.append_event(state, {"ev": "done", "tool_use_id": "o%d" % i, "status": "async_launched", "session": "s"})
        self.assertEqual(collect_runs(state), [])
        self.assertEqual(ledger.build_report(state)["outcomes"]["finished"], 0)

    def test_tune_counts_a_failed_check_as_falling_short(self):
        from pathlib import Path
        from gearshift import ledger
        from gearshift.outcomes import build_tune
        from gearshift.policy import load_policy
        state = Path(self.tmp) / "st2"
        for i in range(20):
            ledger.append_event(state, {"ev": "route", "tool_use_id": "e%d" % i, "class": "edit", "model": "sonnet",
                                        "effort": "medium", "action": "filled"})
            ledger.append_event(state, {"ev": "done", "tool_use_id": "e%d" % i, "verify": "fail" if i < 5 else "pass"})
        t = build_tune(state, policy=load_policy(use_user_files=False))
        self.assertEqual(t["rows"][0]["fell_short"], 5)
        self.assertEqual(t["rows"][0]["verdict"], "raise edit to sonnet@high")


if __name__ == "__main__":
    unittest.main()
