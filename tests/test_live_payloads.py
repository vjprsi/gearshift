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
        event = {"PreToolUse": "pre-agent", "PostToolUse": "post-agent", "SubagentStop": "subagent-stop",
                 "PreSendMessage": "pre-message"}[row["hook"]]
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

    def escalating(self, post, tid=None, words="ESCALATE: the count depends on generated files I cannot see"):
        """A recorded foreground result with my words in it. No pinned agent escalated live."""
        payload = json.loads(json.dumps(post["payload"]))
        payload["tool_response"]["content"][-1]["text"] = words
        if tid:
            payload["tool_use_id"] = tid
        return {"hook": "PostToolUse", "payload": payload}

    def again(self, pre, tid):
        return dict(pre, payload=dict(pre["payload"], tool_use_id=tid))

    def test_a_call_with_a_model_beats_the_pin(self):
        # the fact the next test stands on, as recorded: same agent, same prompt, two calls
        plain, with_model = LIVE["pinned"][2]["payload"], LIVE["pinned"][5]["payload"]
        self.assertEqual(plain["tool_input"]["subagent_type"], "gearshift:scout")
        self.assertNotIn("model", plain["tool_input"])
        self.assertEqual(plain["tool_response"]["resolvedModel"], "claude-haiku-5-5")
        self.assertEqual((with_model["tool_input"]["model"], with_model["tool_input"]["effort"]), ("sonnet", "high"))
        self.assertEqual(with_model["tool_response"]["resolvedModel"], "claude-sonnet-5-5")
        self.assertEqual(LIVE["pinned"][4]["payload"]["effort"], {"level": "high"}, "effort on the call won too")

    def test_escalate_from_a_pinned_agent_then_the_same_agent_again_runs_one_step_up(self):
        # found in review of 1.4.2: the brief promised "one step up" but a pinned agent was
        # returned untouched before the retry was ever looked at, so scout ran on haiku@medium again
        pre, stop, post = LIVE["pinned"][:3]
        self.assertIsNone(self.send(pre), "first time the pin stands")
        self.assertEqual(self.events("route")[-1]["action"], "pinned")
        self.send(stop)
        out = self.send(self.escalating(post))
        self.assertIn("asked to escalate", out["hookSpecificOutput"]["additionalContext"])
        self.assertIn("model=haiku, effort=high", out["hookSpecificOutput"]["additionalContext"])

        out = self.send(self.again(pre, "toolu_second"))
        up = out["hookSpecificOutput"]["updatedInput"]
        self.assertEqual(up["subagent_type"], "gearshift:scout", "same agent, same instructions")
        self.assertEqual(up["effort"], "high")
        self.assertNotIn("model", up, "still haiku, so the pin supplies the model")
        self.assertIn("gearshift:scout -> haiku@high (same task fell short on haiku@medium, so one step up)",
                      out["hookSpecificOutput"]["additionalContext"])
        route = self.events("route")[-1]
        self.assertEqual((route["action"], route["bumped_from"], route["retry_of"]),
                         ("bumped", "haiku@medium", pre["payload"]["tool_use_id"]))

        # and if haiku@high falls short as well, the next one leaves haiku
        self.send(self.escalating(post, tid="toolu_second"))
        out = self.send(self.again(pre, "toolu_third"))
        up = out["hookSpecificOutput"]["updatedInput"]
        self.assertEqual((up["subagent_type"], up["model"], up["effort"]), ("gearshift:scout", "sonnet", "medium"))

    def fell_short_once(self):
        pre, stop, post = LIVE["pinned"][:3]
        self.send(pre)
        self.send(self.escalating(post))
        return pre, post

    def test_pinned_retry_keeps_a_model_claude_passed(self):
        pre, _ = self.fell_short_once()
        named = self.again(pre, "toolu_named")
        named["payload"] = dict(named["payload"], tool_input=dict(pre["payload"]["tool_input"], model="haiku"))
        self.assertIsNone(self.send(named), "a model passed explicitly is kept, as everywhere else")
        self.assertEqual(self.events("route")[-1]["action"], "pinned")

    def test_pinned_retry_in_shadow_mode_is_logged_and_left_alone(self):
        pre, _ = self.fell_short_once()
        self.assertIsNone(self.send(self.again(pre, "toolu_shadow"), GEARSHIFT_MODE="shadow"))
        self.assertEqual(self.events("route")[-1]["action"], "shadow")

    def test_pinned_retry_stops_at_the_ceiling(self):
        # ceiling haiku: haiku@high is still inside it, sonnet is not
        pre, post = self.fell_short_once()
        out = self.send(self.again(pre, "toolu_cap1"), GEARSHIFT_CEILING="haiku")
        self.assertEqual(out["hookSpecificOutput"]["updatedInput"]["effort"], "high")
        self.send(self.escalating(post, tid="toolu_cap1"), GEARSHIFT_CEILING="haiku")
        self.assertIsNone(self.send(self.again(pre, "toolu_cap2"), GEARSHIFT_CEILING="haiku"))
        last = self.events("route")[-1]
        self.assertEqual((last["action"], last["at_ceiling"]), ("pinned", True))

    def test_someone_elses_pinned_agent_is_never_bumped(self):
        pre, stop, post = LIVE["pinned"][:3]
        theirs = json.loads(json.dumps(pre))
        theirs["payload"]["tool_input"]["subagent_type"] = "otherplugin:scout"
        self.send(theirs)
        self.send(self.escalating(post))
        self.assertIsNone(self.send(self.again(theirs, "toolu_theirs2")))

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

    # -- 1.5.0: from two half-hour build sessions and one short session with SendMessage ------

    def test_more_work_sent_with_sendmessage_is_seen_checked_and_sent_back(self):
        # in the first long run 6 of 10 code-changing jobs went to a running fixer this way and
        # the hooks saw none of them
        pre, stop, post, msg, _posted, stop2, stop3 = LIVE["continued"]
        self.assertEqual(msg["payload"]["tool_name"], "SendMessage")
        self.assertEqual(msg["payload"]["tool_input"]["to"], post["payload"]["tool_response"]["agentId"])
        self.send(pre)
        self.send(stop)
        self.send(post)
        self.assertIsNone(self.send(msg, GEARSHIFT_VERIFY=self.failing))
        cont = self.events("continue")[-1]
        self.assertEqual((cont["of"], cont["desc"], cont["mutating"], cont["turn"]),
                         (pre["payload"]["tool_use_id"], "Add transfer_count method", True, 2))
        out = self.send(stop2, GEARSHIFT_VERIFY=self.failing)
        self.assertEqual(out["decision"], "block")
        self.assertEqual(len(self.events("done")), 1, "held, so the second job has no result yet")
        self.assertTrue(stop3["payload"]["stop_hook_active"])
        self.assertIsNone(self.send(stop3, GEARSHIFT_VERIFY=self.failing))
        done = self.events("done")[-1]
        self.assertEqual((done["tool_use_id"], done["of"], done["continued"], done["verify"]),
                         (msg["payload"]["tool_use_id"], pre["payload"]["tool_use_id"], True, "fail"))
        # the task is remembered as having fallen short, so sent again it starts one step up
        out = self.send(dict(pre, payload=dict(pre["payload"], tool_use_id="toolu_again")))
        self.assertEqual(out["hookSpecificOutput"]["updatedInput"]["effort"], "high")

    def test_sendmessage_without_the_hook_is_still_read_at_the_stop(self):
        # belt and braces: if the SendMessage hook did not run, a second stop of a finished agent is the signal
        pre, stop, post, msg, _posted, stop2, stop3 = LIVE["continued"]
        for row in (pre, stop, post):
            self.send(row)
        self.assertIsNone(self.send(stop2))
        done = self.events("done")[-1]
        self.assertTrue(done["continued"])
        self.assertEqual(done["of"], pre["payload"]["tool_use_id"])

    def test_sendmessage_to_someone_else_is_none_of_our_business(self):
        msg = json.loads(json.dumps(LIVE["continued"][3]))
        msg["payload"]["tool_input"]["to"] = "teammate-7"
        self.assertIsNone(self.send(msg))
        with open(os.path.join(self.tmp, "state", "events.jsonl")) if os.path.exists(
                os.path.join(self.tmp, "state", "events.jsonl")) else open(os.devnull) as f:
            self.assertNotIn("continue", f.read())

    def test_an_agent_with_no_edit_or_write_is_never_checked(self):
        # live: 9 of 10 reviews read as code changes ("...fix...") and the suite ran after each one
        pre, stop, post = LIVE["pinned"][:3]            # gearshift:scout: Read, Grep, Glob, Bash
        looks_like_a_change = json.loads(json.dumps(pre))
        looks_like_a_change["payload"]["tool_input"]["prompt"] = "Fix the typo in the error message in store.py."
        looks_like_a_change["payload"]["tool_input"]["description"] = "Fix typo"
        self.send(looks_like_a_change, GEARSHIFT_VERIFY=self.failing)
        route = self.events("route")[-1]
        self.assertEqual((route["mutating"], route["can_write"]), (True, False))
        self.assertIsNone(self.send(post, GEARSHIFT_VERIFY=self.failing))
        self.assertNotIn("verify", self.events("done")[-1])

    def test_an_agent_that_can_edit_is_checked_when_it_did_edit_whatever_the_task_looked_like(self):
        pre, stop, post = LIVE["foreground"][:3]        # general-purpose, and the real result has editFileCount 2
        self.assertEqual(post["payload"]["tool_response"]["toolStats"]["editFileCount"], 2)
        reads_like_a_lookup = json.loads(json.dumps(pre))
        reads_like_a_lookup["payload"]["tool_input"]["prompt"] = "Find where accounts are counted and list the files."
        reads_like_a_lookup["payload"]["tool_input"]["description"] = "Find account counting"
        self.send(reads_like_a_lookup, GEARSHIFT_VERIFY=self.failing)
        self.assertFalse(self.events("route")[-1]["mutating"])
        out = self.send(post, GEARSHIFT_VERIFY=self.failing)
        self.assertIn("your check failed", out["hookSpecificOutput"]["additionalContext"])
        self.assertEqual(self.events("done")[-1]["verify"], "fail")

    def test_can_write_reads_the_definition(self):
        from gearshift.claude_code import can_write
        self.assertIs(can_write("gearshift:reviewer", "pinned", {"tools": "Read, Grep, Glob, Bash"}), False)
        self.assertIs(can_write("gearshift:sorter", "pinned", {"tools": "Read, Grep, Glob, Bash, Write"}), True)
        self.assertIs(can_write("x", "pinned", {"tools": "Read, Bash(git:*), Edit"}), True)
        self.assertIs(can_write("gearshift:fixer", "pinned", {}), True, "no tools line means every tool")
        self.assertIs(can_write("x", "unpinned", {"disallowedTools": "Write, Edit, NotebookEdit"}), False)
        self.assertIs(can_write("Explore", "builtin", {}), False)
        self.assertIs(can_write("general-purpose", "builtin", {}), True)
        self.assertIsNone(can_write("otherplugin:thing", "unknown", {}), "cannot see it, so no claim either way")
        # and the shipped agents say what I think they say
        from gearshift.claude_code import agent_definition
        for name, want in (("scout", False), ("reviewer", False), ("architect", False), ("sorter", True),
                           ("fixer", True), ("builder", True), ("prover", True)):
            status, fm = agent_definition("gearshift:" + name, None)
            self.assertIs(can_write("gearshift:" + name, status, fm), want, name)

    def test_an_escalate_line_in_the_echoed_prompt_is_not_the_agent_escalating(self):
        # the tool response carries the prompt back. Only the agent's answer is read now.
        pre, stop, post = LIVE["foreground"][:3]
        payload = json.loads(json.dumps(post["payload"]))
        self.assertIn("prompt", payload["tool_response"])
        payload["tool_response"]["prompt"] += "\nIf it is beyond you, reply with:\nESCALATE: and one sentence on why"
        self.send(pre)
        self.assertIsNone(self.send({"hook": "PostToolUse", "payload": payload}))
        self.assertNotIn("escalated", self.events("done")[-1])
        from gearshift.outcomes import escalation_in, final_answer
        self.assertIsNone(escalation_in(final_answer({"prompt": "ESCALATE: x", "description": "ESCALATE: y",
                                                      "result": "all fine"})))
        self.assertEqual(escalation_in(final_answer({"prompt": "p", "result": "ESCALATE: too hard"})), "too hard")

    def test_a_similar_task_that_went_fine_is_not_a_retry(self):
        # live: "Run test suite M2" was logged as a retry of "Run test suite M1", 10 of 39 calls
        pre, stop, post = LIVE["pinned"][:3]
        self.send(pre)
        self.send(post)
        self.send(dict(pre, payload=dict(pre["payload"], tool_use_id="toolu_next")))
        route = self.events("route")[-1]
        self.assertNotIn("retry_of", route)
        self.assertNotIn("attempt", route)
        self.assertEqual(route["action"], "pinned")

    def test_a_check_that_runs_out_of_time_is_logged_and_said(self):
        slow = '"%s" -c "import time; time.sleep(30)"' % sys.executable
        pre, stop, post = LIVE["foreground"][:3]
        self.send(pre, GEARSHIFT_VERIFY=slow, GEARSHIFT_VERIFY_TIMEOUT="1")     # 1 is below the floor of 5
        out = self.send(post, GEARSHIFT_VERIFY=slow, GEARSHIFT_VERIFY_TIMEOUT="1")
        note = out["hookSpecificOutput"]["additionalContext"]
        self.assertIn("did not finish within 5 seconds", note)
        self.assertIn("was not checked", note)
        done = self.events("done")[-1]
        self.assertEqual(done["verify"], "timeout")
        from pathlib import Path
        from gearshift import ledger
        text = ledger.format_report(ledger.build_report(Path(self.tmp) / "state"))
        self.assertIn("1 ran out of time", text)
        # unchecked is not failed: the same task again is not a retry
        self.send(dict(pre, payload=dict(pre["payload"], tool_use_id="toolu_after")))
        self.assertNotIn("retry_of", self.events("route")[-1])

    def test_verify_timeout_setting(self):
        from gearshift.config import load_config
        self.assertEqual(load_config(None, env={})["verify_timeout"], 25)
        self.assertEqual(load_config(None, env={"GEARSHIFT_VERIFY_TIMEOUT": "90"})["verify_timeout"], 90)
        self.assertEqual(load_config(None, env={"GEARSHIFT_VERIFY_TIMEOUT": "900"})["verify_timeout"], 120)
        self.assertEqual(load_config(None, env={"GEARSHIFT_VERIFY_TIMEOUT": "soon"})["verify_timeout"], 25)
        with open(os.path.join(self.tmp, ".gearshift.json"), "w") as f:
            json.dump({"verify_timeout": 120}, f)
        self.assertEqual(load_config(self.tmp, env={})["verify_timeout"], 25, "not from a project file")

    def test_debug_log_has_outcomes_not_only_routes(self):
        pre, launch, stop1 = LIVE["background"][:3]
        for row in (pre, launch, stop1):
            self.send(row, GEARSHIFT_DEBUG="1")
        with open(os.path.join(self.tmp, "state", "debug.log")) as f:
            kinds = {line.split(" ")[1] for line in f if line.strip()}
        self.assertTrue({"route", "launched", "usage", "done"} <= kinds, kinds)

    def test_report_counts_background_and_continued(self):
        from pathlib import Path
        from gearshift import ledger
        for row in LIVE["continued"][:4] + LIVE["continued"][5:6]:
            self.send(row)
        for row in LIVE["background"][:3]:
            self.send(row)
        rep = ledger.build_report(Path(self.tmp) / "state")
        self.assertEqual((rep["outcomes"]["finished"], rep["outcomes"]["launched"], rep["outcomes"]["continued"],
                          rep["outcomes"]["continued_finished"]), (2, 1, 1, 1))
        text = ledger.format_report(rep)
        self.assertIn("background: 1 launched", text)
        self.assertIn("more work sent to a running agent (SendMessage): 1 sent, 1 finished", text)

    # -- 2.0.0: probing the pinned reviewer, from the seeded-defect review sessions ----------

    def test_the_pinned_reviewer_is_probed_on_the_calls_the_coin_picks(self):
        pre, launch, stop = LIVE["probed"]
        self.assertEqual(pre["payload"]["tool_input"]["subagent_type"], "gearshift:reviewer")
        self.assertNotIn("effort", pre["payload"]["tool_input"])
        out = self.send(pre, GEARSHIFT_PROBE="0.5")
        hso = out["hookSpecificOutput"]
        self.assertEqual(hso["updatedInput"]["effort"], "medium")
        self.assertNotIn("model", hso["updatedInput"], "effort only: the reviewer stays on Sonnet")
        self.assertEqual(hso["updatedInput"]["prompt"], pre["payload"]["tool_input"]["prompt"], "the brief is not touched")
        self.assertIn("probe", hso["additionalContext"])
        route = self.events("route")[-1]
        self.assertEqual((route["action"], route["probe_from"], route["probe_agent"], route["effort"]),
                         ("probed", "sonnet@high", "gearshift:reviewer", "medium"))
        self.send(launch, GEARSHIFT_PROBE="0.5")
        self.assertIsNone(self.send(stop, GEARSHIFT_PROBE="0.5"))
        self.assertEqual(self.events("done")[-1]["tool_use_id"], pre["payload"]["tool_use_id"])

    def test_the_other_half_and_everyone_with_probing_off_run_as_pinned(self):
        pre = LIVE["not_probed"][0]
        self.assertIsNone(self.send(pre, GEARSHIFT_PROBE="0.5"))
        self.assertEqual(self.events("route")[-1]["action"], "pinned")
        self.assertIsNone(self.send(LIVE["probed"][0]))
        self.assertEqual(self.events("route")[-1]["action"], "pinned")


if __name__ == "__main__":
    unittest.main()
