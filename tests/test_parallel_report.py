"""Two things a real ledger showed on 2026-11-12: parallel hooks losing each other's
session writes, and money going on idle agents re-saving their context."""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tests.helpers import ROOT

from gearshift import claude_code, ledger

LAUNCHER = os.path.join(ROOT, "bin", "gearshift.py")


class SessionFile(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.old = os.environ.get("GEARSHIFT_HOME")
        os.environ["GEARSHIFT_HOME"] = self.tmp

    def tearDown(self):
        if self.old is None:
            os.environ.pop("GEARSHIFT_HOME", None)
        else:
            os.environ["GEARSHIFT_HOME"] = self.old
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_two_hooks_that_read_before_either_saved(self):
        a, b = claude_code.load_session("s"), claude_code.load_session("s")
        a["tasks"] = [{"id": "t1", "short": False}]
        b["tasks"] = [{"id": "t2", "short": False}]
        claude_code.save_session("s", a)
        claude_code.save_session("s", b)
        self.assertEqual([t["id"] for t in claude_code.load_session("s")["tasks"]], ["t1", "t2"])

    def test_what_each_hook_learned_about_one_task_is_kept(self):
        claude_code.save_session("s", {"tasks": [{"id": "t1", "short": False, "tier": "opus"}], "advised": True})
        a, b = claude_code.load_session("s"), claude_code.load_session("s")
        a["tasks"][0]["agent"] = "agent-1"
        b["tasks"][0]["short"] = True
        claude_code.save_session("s", a)
        claude_code.save_session("s", b)
        st = claude_code.load_session("s")
        self.assertEqual((st["tasks"][0]["agent"], st["tasks"][0]["short"], st["advised"]), ("agent-1", True, True))

    def test_a_stale_copy_cannot_undo_a_finish_but_a_new_job_can_reopen(self):
        claude_code.save_session("s", {"tasks": [{"id": "t1", "agent": "a", "done": False, "turn": 1}]})
        stale, fresh = claude_code.load_session("s"), claude_code.load_session("s")
        fresh["tasks"][0]["done"] = True
        claude_code.save_session("s", fresh)
        claude_code.save_session("s", stale)                    # same job, older view
        self.assertTrue(claude_code.load_session("s")["tasks"][0]["done"])
        nxt = claude_code.load_session("s")
        nxt["tasks"][0].update({"done": False, "turn": 2, "cur": "msg-2"})      # SendMessage: more work
        claude_code.save_session("s", nxt)
        t = claude_code.load_session("s")["tasks"][0]
        self.assertEqual((t["done"], t["turn"], t["cur"]), (False, 2, "msg-2"))

    def test_a_dead_lock_file_does_not_stop_anything(self):
        claude_code.save_session("s", {"tasks": [{"id": "t1"}]})
        lock = claude_code._session_file("s").with_suffix(".lock")
        lock.write_text("")
        os.utime(str(lock), (1, 1))                              # left by a hook killed long ago
        claude_code.save_session("s", {"tasks": [{"id": "t2"}]})
        self.assertEqual(len(claude_code.load_session("s")["tasks"]), 2)
        self.assertFalse(lock.exists())

    def test_six_builders_launched_at_once_all_get_a_result(self):
        """The real case: six gearshift:builder agents started in one message, none got a `done`."""
        env = dict(os.environ, HOME=self.tmp, USERPROFILE=self.tmp, GEARSHIFT_HOME=self.tmp,
                   XDG_CONFIG_HOME=os.path.join(self.tmp, ".config"), GEARSHIFT_STOP_DELAY="0")
        for k in list(env):
            if k.startswith(("GEARSHIFT_MODE", "GEARSHIFT_VERIFY", "GEARSHIFT_PROBE", "CLAUDE_PLUGIN_OPTION_",
                             "ANTHROPIC_API_KEY", "GEARSHIFT_API_KEY", "CLAUDE_PROJECT_DIR")):
                env.pop(k)
        transcript = os.path.join(self.tmp, "main.jsonl")
        with open(transcript, "w") as f:
            f.write(json.dumps({"type": "assistant", "version": "2.1.322", "message": {
                "id": "m1", "model": "claude-fable-5-2", "usage": {"input_tokens": 10, "output_tokens": 5}}}) + "\n")
        n = 6

        def together(event, payloads):
            procs = [subprocess.Popen([sys.executable, LAUNCHER, "hook", event], stdin=subprocess.PIPE,
                                      stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env, cwd=self.tmp, text=True)
                     for _ in payloads]
            for p, payload in zip(procs, payloads):
                p.stdin.write(json.dumps(payload))
                p.stdin.close()
            for p in procs:
                code = p.wait(timeout=90)
                err = p.stderr.read()
                p.stdout.close()
                p.stderr.close()
                self.assertEqual(code, 0, err)

        base = {"session_id": "s1", "transcript_path": transcript, "cwd": self.tmp, "tool_name": "Agent"}
        together("pre-agent", [dict(base, tool_use_id="tu%d" % i, tool_input={
            "subagent_type": "gearshift:builder", "description": "Build module %d" % i,
            "prompt": "Implement module number %d of the importer with its tests, across the files it needs." % i})
            for i in range(n)])
        together("post-agent", [dict(base, tool_use_id="tu%d" % i, tool_input={}, tool_response={
            "status": "async_launched", "agentId": "agent-%d" % i}) for i in range(n)])
        subs = []
        for i in range(n):
            path = os.path.join(self.tmp, "agent-%d.jsonl" % i)
            with open(path, "w") as f:
                f.write(json.dumps({"type": "assistant", "message": {
                    "id": "r1", "model": "claude-opus-5-5", "usage": {"input_tokens": 100, "output_tokens": 50},
                    "content": [{"type": "text", "text": "Module %d is in place." % i}]}}) + "\n")
            subs.append({"session_id": "s1", "agent_id": "agent-%d" % i, "agent_type": "gearshift:builder",
                         "agent_transcript_path": path, "cwd": self.tmp})
        together("subagent-stop", subs)
        events = ledger.read_events(Path(self.tmp), None)
        done = sorted(e["tool_use_id"] for e in events if e["ev"] == "done")
        self.assertEqual(done, sorted("tu%d" % i for i in range(n)))
        rep = ledger.build_report(Path(self.tmp))
        self.assertEqual((rep["outcomes"]["launched"], rep["outcomes"]["finished"], rep["launched_no_result"]), (n, n, 0))


class Report(unittest.TestCase):
    def setUp(self):
        self.state = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(str(self.state), ignore_errors=True)

    def usage(self, agent, cost, out, cache_write, model="claude-opus-5-5", agent_type="gearshift:builder"):
        ledger.append_event(self.state, {
            "ev": "usage", "agent_id": agent, "agent_type": agent_type, "main_model": model, "requests": 10,
            "cost_usd": cost, "session_rate_cost_usd": cost * 1.5,
            "by_model": {model: {"input_tokens": 1000, "output_tokens": out, "cache_read_input_tokens": 500000,
                                 "cache_write_5m_tokens": cache_write, "cache_write_1h_tokens": 0}},
            "cost_by_model": {model: cost}})

    def test_biggest_runs_and_idle_resaves(self):
        ledger.append_event(self.state, {"ev": "route", "tool_use_id": "tu1", "desc": "Build the importer",
                                         "action": "pinned"})
        ledger.append_event(self.state, {"ev": "launched", "tool_use_id": "tu1", "agent_id": "a1"})
        self.usage("a1", 12.60, 40000, 600000)
        self.usage("a1", 19.87, 46000, 1000000)        # woke up: +400k cache written, +6k output... not idle
        self.usage("a1", 27.14, 47000, 1400000)        # woke up again: +400k written, +1k output, +$7.27
        self.usage("a2", 3.00, 20000, 200000)
        self.usage("a2", 6.00, 60000, 400000)          # real work: lots of output
        self.usage("a3", 0.15, 800, 5000, model="claude-haiku-5-5", agent_type="gearshift:scout")
        rep = ledger.build_report(self.state)
        self.assertEqual([b["cost_usd"] for b in rep["biggest_runs"]], [27.14, 6.0, 0.15])
        self.assertEqual((rep["biggest_runs"][0]["desc"], rep["biggest_runs"][0]["tier"]), ("Build the importer", "opus"))
        self.assertAlmostEqual(rep["idle_resave_usd"], 7.27, places=2)
        self.assertEqual((rep["idle_resave_agents"], rep["launched_no_result"]), (1, 1))
        text = ledger.format_report(rep)
        self.assertIn("Biggest runs:", text)
        self.assertIn("Build the importer", text)
        self.assertIn("About $7.27 of this (22%) went on 1 agent re-saving", text)
        self.assertIn("1 background agents have no result recorded", text)

    def test_quiet_when_there_is_nothing_to_say(self):
        self.usage("a1", 0.02, 500, 1000, model="claude-haiku-5-5", agent_type="gearshift:scout")
        text = ledger.format_report(ledger.build_report(self.state))
        for phrase in ("Biggest runs", "re-saving", "no result recorded"):
            self.assertNotIn(phrase, text)


if __name__ == "__main__":
    unittest.main()
