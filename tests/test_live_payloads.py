"""Replays of what Claude Code really sent the hooks.

tests/live_payloads.json was recorded on 2026-11-10 from two headless sessions
(Claude Code 2.1.322, Windows): one foreground subagent and one background
subagent, each changing code, each sent twice. Only local paths were removed.
Until then every hook test ran on payloads I had written by hand.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

from tests.helpers import ROOT, TESTS

LAUNCHER = os.path.join(ROOT, "bin", "gearshift.py")

with open(os.path.join(TESTS, "live_payloads.json"), encoding="utf-8") as _f:
    LIVE = json.load(_f)


class LivePayloads(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.env = dict(os.environ)
        self.env.update({"HOME": self.tmp, "USERPROFILE": self.tmp, "GEARSHIFT_HOME": os.path.join(self.tmp, "state"),
                         "XDG_CONFIG_HOME": os.path.join(self.tmp, ".config"), "GEARSHIFT_STOP_DELAY": "0"})
        for k in list(self.env):
            if k.startswith(("CLAUDE_PLUGIN_OPTION_", "GEARSHIFT_MODE", "GEARSHIFT_PROBE", "GEARSHIFT_VERIFY",
                             "CLAUDE_PROJECT_DIR", "ANTHROPIC_API_KEY", "GEARSHIFT_API_KEY", "GEARSHIFT_JUDGE",
                             "CLAUDE_CODE_EFFORT_LEVEL")):
                self.env.pop(k)
        self.transcript = os.path.join(self.tmp, "main.jsonl")
        with open(self.transcript, "w") as f:
            f.write(json.dumps({"type": "assistant", "version": "2.1.322",
                                "message": {"id": "m1", "model": "claude-sonnet-5-5",
                                            "usage": {"input_tokens": 10, "output_tokens": 5}}}) + "\n")
        self.sub = os.path.join(self.tmp, "sub.jsonl")
        with open(self.sub, "w") as f:
            f.write(json.dumps({"type": "assistant", "message": {"id": "r1", "model": "claude-sonnet-5-5",
                                "usage": {"input_tokens": 100, "output_tokens": 50},
                                "content": [{"type": "text", "text": "working"}]}}) + "\n")
        # a long command on purpose: the one in the live run was 130 characters and got cut
        self.failing = '"%s" -c "import sys; print(\'CHECK-FAIL-MARKER\'); %s sys.exit(1)"' % (
            sys.executable, "pass; " * 25)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def send(self, row, **env):
        """One recorded payload through the real launcher, with the paths we removed put back."""
        payload = dict(row["payload"], transcript_path=self.transcript, cwd=self.tmp)
        if row["hook"] == "SubagentStop":
            payload["agent_transcript_path"] = self.sub
        event = {"PreToolUse": "pre-agent", "PostToolUse": "post-agent", "SubagentStop": "subagent-stop"}[row["hook"]]
        e = dict(self.env)
        e.update(env)
        p = subprocess.run([sys.executable, LAUNCHER, "hook", event], input=json.dumps(payload),
                           capture_output=True, text=True, env=e, cwd=self.tmp, timeout=60)
        self.assertEqual(p.returncode, 0, p.stderr)
        return json.loads(p.stdout) if p.stdout.strip() else None

    def events(self, kind):
        with open(os.path.join(self.tmp, "state", "events.jsonl")) as f:
            return [e for e in map(json.loads, f) if e["ev"] == kind]

    def test_background_launch_and_finish_pair_up_by_agent_id(self):
        pre, launch, stop1, stop2 = LIVE["background"][:4]
        self.assertEqual(launch["payload"]["tool_response"]["status"], "async_launched")
        self.assertEqual(launch["payload"]["tool_response"]["agentId"], stop1["payload"]["agent_id"],
                         "the id at launch is the id at SubagentStop: the 1.4.1 fix stands on this")
        self.send(pre)
        self.assertIsNone(self.send(launch))
        self.assertEqual(self.events("done"), [])
        self.send(stop1)
        done = self.events("done")
        self.assertEqual(len(done), 1)
        self.assertEqual(done[0]["tool_use_id"], pre["payload"]["tool_use_id"])
        self.assertEqual(done[0]["agent_id"], self.events("launched")[0]["agent_id"])

    def test_background_failing_check_holds_it_then_escalation_then_one_step_up(self):
        pre, launch, stop1, stop2, pre_again = LIVE["background"][:5]
        self.send(pre, GEARSHIFT_VERIFY=self.failing)
        self.send(launch, GEARSHIFT_VERIFY=self.failing)
        out = self.send(stop1, GEARSHIFT_VERIFY=self.failing)
        self.assertEqual(out["decision"], "block")
        self.assertIn("CHECK-FAIL-MARKER", out["reason"])
        self.assertIn(self.failing, out["reason"], "the whole command, so the subagent can run it again")
        self.assertEqual(self.events("done"), [])
        # what the subagent really answered after being held: stop_hook_active true, ESCALATE first
        self.assertTrue(stop2["payload"]["stop_hook_active"])
        self.assertTrue(stop2["payload"]["last_assistant_message"].startswith("ESCALATE:"))
        self.assertIsNone(self.send(stop2, GEARSHIFT_VERIFY=self.failing))
        done = self.events("done")[-1]
        self.assertTrue(done["escalated"])
        self.assertIn("forced failure", done["escalate_reason"])
        # the main session sent the identical call again
        out = self.send(pre_again, GEARSHIFT_VERIFY=self.failing)
        up = out["hookSpecificOutput"]["updatedInput"]
        self.assertEqual(up["effort"], "high")
        self.assertNotIn("model", up, "same tier as the session, so it inherits")
        route = self.events("route")[-1]
        self.assertEqual((route["retry_of"], route["bumped_from"]), (pre["payload"]["tool_use_id"], "sonnet@medium"))

    def test_foreground_result_shape_and_failing_check(self):
        pre, stop, post = LIVE["foreground"][:3]
        resp = post["payload"]["tool_response"]
        self.assertEqual(resp["status"], "completed")
        self.assertEqual(resp["agentId"], stop["payload"]["agent_id"])
        self.send(pre, GEARSHIFT_VERIFY=self.failing)
        # in the foreground SubagentStop comes BEFORE PostToolUse, and nothing ties it to a task yet
        self.assertIsNone(self.send(stop, GEARSHIFT_VERIFY=self.failing))
        self.assertEqual(self.events("done"), [])
        out = self.send(post, GEARSHIFT_VERIFY=self.failing)
        ctx = out["hookSpecificOutput"]["additionalContext"]
        self.assertIn("Do not build on this result", ctx)
        self.assertIn("model=sonnet, effort=high", ctx)
        self.assertIn(self.failing, ctx)
        done = self.events("done")[-1]
        self.assertEqual((done["status"], done["verify"], done["resolved_model"], done["tool_uses"]),
                         ("completed", "fail", "claude-sonnet-5-5", 8))
        self.assertIsInstance(done["duration_ms"], int)
        self.assertNotIn("escalated", done, "a normal answer must not read as an escalation")

    def test_foreground_escalation_in_the_real_result_shape(self):
        # the shape is real, the words are mine: no foreground subagent escalated in the live runs
        pre, stop, post = LIVE["foreground"][:3]
        payload = json.loads(json.dumps(post["payload"]))
        payload["tool_response"]["content"][-1]["text"] = "ESCALATE: the fee rounding needs a decision I cannot make"
        self.send(pre)
        out = self.send({"hook": "PostToolUse", "payload": payload})
        self.assertIn("asked to escalate", out["hookSpecificOutput"]["additionalContext"])
        self.assertTrue(self.events("done")[-1]["escalated"])

    def test_retry_at_the_ceiling_does_not_drop_below_the_rung_that_failed(self):
        # live: sonnet session, failed on sonnet@medium, then on sonnet@high. The third try used to go
        # back to sonnet@medium and still say "one step up".
        rows = LIVE["foreground"]
        for r in rows:
            out = self.send(r, GEARSHIFT_VERIFY=self.failing)
        self.assertIn("no step above it", out["hookSpecificOutput"]["additionalContext"])
        third = dict(rows[0], payload=dict(rows[0]["payload"], tool_use_id="toolu_third"))
        out = self.send(third, GEARSHIFT_VERIFY=self.failing)
        self.assertEqual(out["hookSpecificOutput"]["updatedInput"]["effort"], "high")
        note = out["hookSpecificOutput"]["additionalContext"]
        self.assertIn("no step above it within your ceiling", note)
        self.assertNotIn("so one step up", note)
        self.assertTrue(self.events("route")[-1]["at_ceiling"])

    def test_a_reworded_retry_is_still_the_same_task(self):
        # both calls are word for word what the main session sent in the fifth live run. The second
        # one was not recognised as a retry and started on the same rung again.
        from gearshift.outcomes import find_prior, fingerprint
        task = ("Add a method account_count() to the Wallet class in walletcore/wallet.py that returns the number "
                "of accounts as an int. Add one unit test for it in tests/test_accounts.py and run python -m "
                "unittest tests.test_accounts.")
        first = ("Add Wallet.account_count method", task + " Report what you changed and the test result.")
        again = ("Retry Wallet.account_count task", task + " Note: a previous attempt may already have added "
                 "account_count() and test_account_count; check for them first and don't duplicate. Report what "
                 "you changed and the exact test result, including any errors or blocked commands.")
        fp = lambda pair: fingerprint(pair[0] + "\n" + pair[1])
        short = [{"id": "t1", "fp": fp(first), "desc": first[0], "short": True}]
        self.assertEqual(find_prior(short, fp(again), again[0])["id"], "t1")
        fine = [dict(short[0], short=False)]
        self.assertIsNone(find_prior(fine, fp(again), again[0]), "only a task that fell short gets the looser match")
        other = ("Add fee report", "Add a method fee_total() to the Wallet class in walletcore/wallet.py that "
                 "returns all fees collected as an int, with one unit test in tests/test_fees.py, then run python "
                 "-m unittest tests.test_fees and report the result.")
        self.assertIsNone(find_prior(short, fp(other), other[0]), "a different change to the same class is not a retry")

    def test_a_held_subagent_is_asked_for_its_whole_report_again(self):
        # live: the main session only got the second, short reply ("unchanged from my earlier report")
        pre, launch, stop1 = LIVE["background"][:3]
        self.send(pre, GEARSHIFT_VERIFY=self.failing)
        self.send(launch, GEARSHIFT_VERIFY=self.failing)
        out = self.send(stop1, GEARSHIFT_VERIFY=self.failing)
        self.assertIn("your earlier reply was not delivered", out["reason"])

    def test_brief_tells_the_main_session_what_an_escalate_reply_means(self):
        # live: the background subagent answered ESCALATE: and the main session did nothing with it,
        # because nothing had told it to. PostToolUse cannot say it: for a background agent it fires at launch.
        p = subprocess.run([sys.executable, LAUNCHER, "hook", "session-start"],
                           input=json.dumps({"session_id": "s9", "cwd": self.tmp, "source": "startup"}),
                           capture_output=True, text=True, env=self.env, cwd=self.tmp, timeout=60)
        brief = json.loads(p.stdout)["hookSpecificOutput"]["additionalContext"]
        self.assertIn("starts with ESCALATE:", brief)
        self.assertIn("send the same task again", brief)


if __name__ == "__main__":
    unittest.main()
