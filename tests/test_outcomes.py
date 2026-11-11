import os
import shutil
import tempfile
import unittest
from pathlib import Path

from gearshift import ledger
from gearshift.outcomes import (build_tune, escalation_in, find_prior, fingerprint, format_tune,
                                next_rung, similarity)
from gearshift.policy import load_policy

POL = load_policy(use_user_files=False)


class Signals(unittest.TestCase):
    def test_escalate_line(self):
        self.assertEqual(escalation_in({"content": [{"type": "text", "text": "ESCALATE: needs a design call"}]}),
                         "needs a design call")
        self.assertIsNotNone(escalation_in({"result": "I looked.\n\n**ESCALATE**: three services disagree"}))
        self.assertIsNotNone(escalation_in("notes\n  ESCALATE:"))

    def test_not_an_escalation(self):
        for text in ("We might escalate: later.", "escalate: lower case is just prose",
                     "The ESCALATE convention is documented in the skill.", ""):
            self.assertIsNone(escalation_in({"content": [{"type": "text", "text": text}]}), text)
        self.assertIsNone(escalation_in({"status": "completed", "n": 3, "x": None}))

    def test_same_task_detection(self):
        a = "Find all API endpoints defined in this repo and list the handler files."
        fa = fingerprint(a)
        self.assertGreater(similarity(fa, fingerprint(a + " Look harder, including generated routes.")), 0.6)
        self.assertLess(similarity(fa, fingerprint("Write the release notes for version 2.4 from the changelog.")), 0.2)
        tasks = [{"id": "t1", "fp": fa, "desc": "Find API endpoints"},
                 {"id": "t2", "fp": fingerprint("Review the payment retry logic for race conditions"), "desc": "Review"}]
        self.assertEqual(find_prior(tasks, fingerprint(a), "")["id"], "t1")
        self.assertIsNone(find_prior(tasks, fingerprint("Summarise yesterday's deploy log"), ""))
        self.assertNotIn("endpoints", " ".join(fa), "the state file must not hold the prompt words")

    def test_next_rung_walks_the_ladder(self):
        self.assertEqual(next_rung("haiku", "medium", POL), ("haiku", "high"))
        self.assertEqual(next_rung("haiku", "high", POL), ("sonnet", "medium"))
        self.assertEqual(next_rung("sonnet", "high", POL), ("opus", "medium"))
        self.assertEqual(next_rung("opus", "xhigh", POL), ("fable", "high"))
        self.assertIsNone(next_rung("fable", "xhigh", POL))
        self.assertIsNone(next_rung("sonnet", "high", POL, ceiling="sonnet"))


class Tune(unittest.TestCase):
    def setUp(self):
        self.state = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(str(self.state), ignore_errors=True)

    def add(self, n, cls, tier, effort="medium", escalated=0, failed=0, action="filled", start=0):
        for i in range(n):
            tid = "%s-%s-%d" % (cls, tier, start + i)
            ledger.append_event(self.state, {"ev": "route", "tool_use_id": tid, "class": cls, "model": tier,
                                             "effort": effort, "action": action, "session": "s"})
            done = {"ev": "done", "tool_use_id": tid, "agent_id": "a" + tid, "status": "completed"}
            if i < escalated:
                done["escalated"] = True
            elif i < escalated + failed:
                done["failed"] = True
            ledger.append_event(self.state, done)
            ledger.append_event(self.state, {"ev": "usage", "agent_id": "a" + tid, "cost_usd": 0.02})

    def row(self, t, cls, tier):
        return [r for r in t["rows"] if (r["class"], r["tier"]) == (cls, tier)][0]

    def test_empty_ledger(self):
        t = build_tune(self.state, policy=POL)
        self.assertEqual((t["runs"], t["rows"], t["policy_overrides"]), (0, [], None))
        self.assertIn("Nothing to learn from yet", format_tune(t))

    def test_proposes_a_raise_only_with_enough_evidence(self):
        self.add(30, "edit", "sonnet", escalated=6, failed=1)      # 23% fell short on its home tier
        self.add(8, "review", "sonnet", "high", escalated=4)       # 50% but only 8 runs
        self.add(40, "lookup", "haiku", escalated=1)               # 2.5%: fine
        t = build_tune(self.state, policy=POL)
        self.assertEqual(self.row(t, "edit", "sonnet")["verdict"], "raise edit to sonnet@high")
        self.assertIn("not enough runs", self.row(t, "review", "sonnet")["verdict"])
        self.assertEqual(self.row(t, "lookup", "haiku")["verdict"], "keep")
        self.assertEqual(t["policy_overrides"], {"classes": {"edit": {"tier": "sonnet", "effort": "high"}}})
        self.assertAlmostEqual(self.row(t, "edit", "sonnet")["avg_cost_usd"], 0.02)
        # and the proposal is a valid policy override
        pol = load_policy(t["policy_overrides"], use_user_files=False)
        self.assertEqual((pol.classes["edit"]["tier"], pol.classes["edit"]["effort"]), ("sonnet", "high"))
        self.assertIn('"effort": "high"', format_tune(t))

    def test_never_proposes_going_down(self):
        self.add(60, "build", "opus")
        t = build_tune(self.state, policy=POL)
        self.assertIsNone(t["policy_overrides"])
        self.assertIn("gearshift bench", self.row(t, "build", "opus")["verdict"])

    def test_runs_off_the_home_tier_do_not_move_the_policy(self):
        self.add(30, "edit", "haiku", escalated=20, action="kept")     # Claude sent edits to haiku itself
        t = build_tune(self.state, policy=POL)
        self.assertIsNone(t["policy_overrides"])
        self.assertIn("not where the policy sends edit", self.row(t, "edit", "haiku")["verdict"])

    def test_retried_higher_counts_as_falling_short(self):
        for i in range(20):
            ledger.append_event(self.state, {"ev": "route", "tool_use_id": "a%d" % i, "class": "lookup",
                                             "model": "haiku", "effort": "medium", "action": "filled"})
            ledger.append_event(self.state, {"ev": "done", "tool_use_id": "a%d" % i, "status": "completed"})
            if i < 5:
                ledger.append_event(self.state, {"ev": "route", "tool_use_id": "b%d" % i, "class": "lookup",
                                                 "model": "sonnet", "action": "kept", "retry_of": "a%d" % i})
        t = build_tune(self.state, policy=POL)
        r = self.row(t, "lookup", "haiku")
        self.assertEqual((r["runs"], r["retried_higher"], r["fell_short"]), (20, 5, 5))
        self.assertEqual(r["verdict"], "raise lookup to haiku@high")

    def test_cli(self):
        import subprocess, sys
        from tests.helpers import ROOT
        self.add(30, "edit", "sonnet", escalated=6)
        env = dict(os.environ, GEARSHIFT_HOME=str(self.state), PYTHONPATH=os.path.join(ROOT, "src"),
                   HOME=str(self.state), USERPROFILE=str(self.state))
        p = subprocess.run([sys.executable, "-m", "gearshift", "tune"], capture_output=True, text=True, env=env)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("raise edit to sonnet@high", p.stdout)


if __name__ == "__main__":
    unittest.main()
