import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tests.helpers import ROOT

from gearshift import ledger
from gearshift.claude_code import decide_agent_call
from gearshift.config import DEFAULTS, load_config
from gearshift.outcomes import build_tune, format_tune, prev_rung, probe_ok, sampled
from gearshift.policy import load_policy

POL = load_policy(use_user_files=False)
REVIEW = {"subagent_type": "general-purpose", "description": "Review the diff",
          "prompt": "Review this pull request for logic errors and report what you find. Do not change any files."}
EDIT = {"subagent_type": "general-purpose", "description": "Fix typo",
        "prompt": "Fix the typo in the error message in login.py."}
RISKY = {"subagent_type": "general-purpose", "description": "Review payments",
         "prompt": "Review the production payment retry logic for race conditions and report what you find."}


def decide(tool_input, probe, **over):
    cfg = dict(DEFAULTS, judge="off")
    cfg.update(over)
    return decide_agent_call(tool_input, cfg=cfg, session_tier="opus", version="2.1.322",
                             project_dir=None, probe=probe)


class Probe(unittest.TestCase):
    def test_prev_rung(self):
        self.assertEqual(prev_rung("sonnet", "high", POL), ("sonnet", "medium"))
        self.assertEqual(prev_rung("sonnet", "medium", POL), ("haiku", "high"))
        self.assertEqual(prev_rung("opus", "medium", POL), ("sonnet", "high"))
        self.assertIsNone(prev_rung("haiku", "low", POL))

    def test_what_may_be_probed(self):
        self.assertTrue(probe_ok("review", []))
        self.assertTrue(probe_ok("lookup", ["checkable", "read_only"]))
        for cls in ("edit", "build", "frontier", "unsure"):
            self.assertFalse(probe_ok(cls, []), cls)
        for flag in ("mutating", "stakes", "tricky", "long_horizon"):
            self.assertFalse(probe_ok("review", [flag]), flag)

    def test_sampling_is_deterministic_and_about_right(self):
        self.assertFalse(sampled("anything", 0))
        hits = sum(sampled("call-%d" % i, 0.1) for i in range(5000))
        self.assertTrue(350 < hits < 650, hits)
        self.assertEqual(sampled("call-7", 0.1), sampled("call-7", 0.1))

    def test_probe_goes_one_step_down(self):
        base, probed = decide(REVIEW, False), decide(REVIEW, True)
        self.assertEqual((base["model"], base["effort"]), ("sonnet", "high"))
        self.assertEqual((probed["model"], probed["effort"], probed["probe_from"]), ("sonnet", "medium", "sonnet@high"))

    def test_probe_never_touches_code_changes_or_risky_work(self):
        for task in (EDIT, RISKY):
            base, probed = decide(task, False), decide(task, True)
            self.assertNotIn("probe_from", probed, task["description"])
            self.assertEqual((probed["model"], probed["effort"]), (base["model"], base["effort"]))

    def test_probe_respects_an_explicit_model_and_strict_mode(self):
        self.assertNotIn("probe_from", decide(dict(REVIEW, model="sonnet"), True))
        self.assertNotIn("probe_from", decide(REVIEW, True, mode="strict"))

    def test_setting_is_clamped_and_a_project_file_cannot_turn_it_on(self):
        self.assertEqual(load_config(env={"GEARSHIFT_PROBE": "0.1"})["probe"], 0.1)
        self.assertEqual(load_config(env={"GEARSHIFT_PROBE": "5"})["probe"], 0.5)
        self.assertEqual(load_config(env={"GEARSHIFT_PROBE": "lots"})["probe"], 0.0)
        tmp = tempfile.mkdtemp()
        try:
            with open(os.path.join(tmp, ".gearshift.json"), "w") as f:
                json.dump({"probe": 0.5}, f)
            self.assertEqual(load_config(tmp, env={})["probe"], 0.0)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class State(unittest.TestCase):
    def setUp(self):
        self.state = Path(tempfile.mkdtemp())
        self.env = dict(os.environ, GEARSHIFT_HOME=str(self.state), HOME=str(self.state), USERPROFILE=str(self.state),
                        XDG_CONFIG_HOME=str(self.state / ".config"), PYTHONPATH=os.path.join(ROOT, "src"))
        for k in list(self.env):
            if k.startswith(("GEARSHIFT_MODE", "GEARSHIFT_PROBE", "CLAUDE_PLUGIN_OPTION_")):
                self.env.pop(k)

    def tearDown(self):
        shutil.rmtree(str(self.state), ignore_errors=True)

    def probes(self, n, short, cls="review", ran=("sonnet", "medium"), home="sonnet@high"):
        for i in range(n):
            tid = "p-%s-%d" % (cls, i)
            ledger.append_event(self.state, {"ev": "route", "tool_use_id": tid, "class": cls, "model": ran[0],
                                             "effort": ran[1], "action": "filled", "probe_from": home, "session": "s1"})
            done = {"ev": "done", "tool_use_id": tid, "agent_id": "a" + tid, "session": "s1"}
            if i < short:
                done["escalated"] = True
            ledger.append_event(self.state, done)

    def test_tune_lowers_a_class_only_when_probes_hold_up(self):
        self.probes(20, 0)
        t = build_tune(self.state, policy=POL)
        self.assertEqual(t["policy_overrides"], {"classes": {"review": {"tier": "sonnet", "effort": "medium"}}})
        text = format_tune(t)
        self.assertIn("review can move down to sonnet@medium", text)
        self.assertIn("Read a few of the probe results", text)

    def test_tune_keeps_the_class_when_probes_fall_short(self):
        self.probes(20, 6)
        t = build_tune(self.state, policy=POL)
        self.assertIsNone(t["policy_overrides"])
        self.assertIn("keep review on sonnet@high", t["probes"][0]["verdict"])

    def test_tune_wants_enough_probes(self):
        self.probes(6, 0)
        t = build_tune(self.state, policy=POL)
        self.assertIsNone(t["policy_overrides"])
        self.assertIn("not enough probes", t["probes"][0]["verdict"])

    def test_probes_do_not_count_against_the_home_tier(self):
        self.probes(20, 20)      # every probe failed: that says nothing about sonnet@high itself
        self.assertEqual(build_tune(self.state, policy=POL)["rows"], [])

    def line(self, payload, **env):
        e = dict(self.env)
        e.update(env)
        p = subprocess.run([sys.executable, "-m", "gearshift", "statusline"], input=payload,
                           capture_output=True, text=True, env=e, timeout=30)
        self.assertEqual(p.returncode, 0, p.stderr)
        return p.stdout.strip()

    def test_statusline(self):
        self.assertEqual(self.line(json.dumps({"session_id": "s1"})), "gearshift fill | no subagents yet")
        for i, (tier, action) in enumerate([("haiku", "filled"), ("haiku", "filled"), ("sonnet", "filled"),
                                            (None, "kept")]):
            ledger.append_event(self.state, {"ev": "route", "session": "s1", "tool_use_id": "t%d" % i,
                                             "model": tier, "action": action})
        ledger.append_event(self.state, {"ev": "route", "session": "other", "tool_use_id": "x", "model": "opus",
                                         "action": "filled"})
        ledger.append_event(self.state, {"ev": "done", "session": "s1", "tool_use_id": "t0", "escalated": True})
        ledger.append_event(self.state, {"ev": "usage", "session": "s1", "agent_id": "a0", "cost_usd": 0.015,
                                         "session_rate_cost_usd": 0.31, "main_model": "claude-haiku-5-5"})
        self.assertEqual(self.line(json.dumps({"session_id": "s1", "model": {"display_name": "Fable"}})),
                         "gearshift fill | 4 subagents: haiku 1 | hook set 3 | $0.015 vs $0.31 at session rates | 1 escalated")

    def test_statusline_counts_pinned_agents_claude_picked_itself(self):
        # what the first live session looked like: every call went to a pinned gearshift agent
        for i, model in enumerate(["claude-haiku-5-5"] * 8 + ["claude-sonnet-5-5"] * 3):
            ledger.append_event(self.state, {"ev": "route", "session": "s1", "tool_use_id": "t%d" % i,
                                             "action": "pinned", "agent_type": "gearshift:scout"})
            ledger.append_event(self.state, {"ev": "usage", "session": "s1", "agent_id": "a%d" % i, "main_model": model,
                                             "cost_usd": 0.02, "session_rate_cost_usd": 0.08})
        self.assertEqual(self.line(json.dumps({"session_id": "s1"})),
                         "gearshift fill | 11 subagents: haiku 8, sonnet 3 | $0.220 vs $0.88 at session rates")
        self.assertEqual(self.line(json.dumps({"session_id": "s1"}), GEARSHIFT_MODE="off"), "gearshift off")

    def test_statusline_never_breaks(self):
        for junk in ("", "not json", "[1, 2]", '{"session_id": 5, "workspace": "x"}'):
            self.assertTrue(self.line(junk).startswith("gearshift"), junk)
        with open(self.state / "events.jsonl", "w") as f:
            f.write("{broken\n\n[]\n")
        self.assertEqual(self.line('{"session_id": "s1"}'), "gearshift fill | no subagents yet")

    def test_statusline_install_snippet(self):
        p = subprocess.run([sys.executable, os.path.join(ROOT, "bin", "gearshift.py"), "statusline", "--install"],
                           capture_output=True, text=True, env=self.env)
        snippet = json.loads(p.stdout[p.stdout.index("{"):])
        self.assertEqual(snippet["statusLine"]["type"], "command")
        self.assertIn("statusline", snippet["statusLine"]["command"])


if __name__ == "__main__":
    unittest.main()
