import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

from tests.helpers import ROOT

from gearshift.claude_code import decide_agent_call
from gearshift.config import DEFAULTS
from gearshift.outcomes import HINT

LAUNCHER = os.path.join(ROOT, "bin", "gearshift.py")
SH_LAUNCHER = os.path.join(ROOT, "bin", "gearshift")

LOOKUP = {"subagent_type": "general-purpose", "description": "Find API endpoints",
          "prompt": "Find all API endpoints defined in this repo and list the handler files."}
BUILD = {"subagent_type": "general-purpose", "description": "Implement bulk import",
         "prompt": "Implement bulk CSV import for contacts: API endpoint, background job, React page, and tests."}
DESIGN = {"subagent_type": "general-purpose", "description": "Plan the migration",
          "prompt": "Plan the database migration from MySQL to Postgres with zero downtime."}


GERMAN = {"subagent_type": "general-purpose", "description": "Dateien finden",
          "prompt": "Finde alle Dateien, die das Zahlungsmodul importieren, und gib mir die Liste."}


def cfg(**over):
    c = dict(DEFAULTS, judge="off")     # tests must never call a real model
    c.update(over)
    return c


def decide(tool_input, session_tier="opus", version="2.1.322", project_dir=None, **over):
    rec = decide_agent_call(tool_input, cfg=cfg(**over), session_tier=session_tier,
                            version=version, project_dir=project_dir)
    return rec


class DecideAgentCall(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp()
        self._old = {k: os.environ.get(k) for k in ("HOME", "USERPROFILE", "CLAUDE_CODE_EFFORT_LEVEL", "XDG_CONFIG_HOME")}
        os.environ["HOME"] = os.environ["USERPROFILE"] = self.home
        os.environ["XDG_CONFIG_HOME"] = os.path.join(self.home, ".config")
        os.environ.pop("CLAUDE_CODE_EFFORT_LEVEL", None)

    def tearDown(self):
        for k, v in self._old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(self.home, ignore_errors=True)

    def test_fill_routes_lookup_down(self):
        rec = decide(LOOKUP)
        self.assertEqual(rec["action"], "filled")
        self.assertEqual(rec["updated"]["model"], "haiku")
        self.assertEqual(rec["updated"]["effort"], "medium")
        # updatedInput replaces the whole input, so every original field must survive
        for k, v in LOOKUP.items():
            self.assertEqual(rec["updated"][k], v)

    def test_explicit_model_is_kept_in_fill_mode(self):
        rec = decide(dict(LOOKUP, model="opus"))
        self.assertEqual(rec["action"], "kept")
        self.assertIsNone(rec["updated"])

    def test_strict_replaces_explicit_model(self):
        rec = decide(dict(LOOKUP, model="opus"), mode="strict")
        self.assertEqual(rec["action"], "overrode")
        self.assertEqual(rec["updated"]["model"], "haiku")

    def test_shadow_changes_nothing(self):
        rec = decide(LOOKUP, mode="shadow")
        self.assertEqual(rec["action"], "shadow")
        self.assertIsNone(rec["updated"])
        self.assertEqual(rec["model"], "haiku")

    def test_same_tier_as_session_inherits_and_sets_effort(self):
        rec = decide(BUILD, session_tier="opus")
        self.assertEqual(rec["action"], "effort-only")
        self.assertNotIn("model", rec["updated"])
        self.assertEqual(rec["updated"]["effort"], "medium")

    def test_never_routes_above_the_session_by_default(self):
        rec = decide(DESIGN, session_tier="sonnet")
        self.assertNotEqual((rec["updated"] or {}).get("model"), "opus")
        self.assertEqual(rec.get("capped_from"), "opus")
        self.assertEqual(rec["updated"]["effort"], "high")   # capped: more effort instead
        self.assertNotIn("model", rec["updated"])

    def test_explicit_ceiling_allows_routing_up(self):
        rec = decide(DESIGN, session_tier="sonnet", ceiling="opus")
        self.assertEqual(rec["updated"]["model"], "opus")

    def test_unknown_session_never_guesses_upward(self):
        rec = decide(DESIGN, session_tier=None)
        self.assertEqual(rec["action"], "inherit")
        self.assertIsNone(rec["updated"])
        rec = decide(LOOKUP, session_tier=None)
        self.assertEqual(rec["updated"]["model"], "haiku")

    def test_project_cap(self):
        rec = decide(BUILD, session_tier="opus", project_cap="sonnet")
        self.assertEqual(rec["updated"]["model"], "sonnet")

    def test_effort_only_on_versions_that_accept_it(self):
        self.assertNotIn("effort", decide(LOOKUP, version="2.1.280")["updated"])
        self.assertNotIn("effort", decide(LOOKUP, version=None)["updated"])
        self.assertIn("effort", decide(LOOKUP, version="2.1.292")["updated"])
        self.assertIn("effort", decide(LOOKUP, version=None, set_effort="always")["updated"])
        self.assertNotIn("effort", decide(LOOKUP, set_effort="never")["updated"])

    def test_env_effort_override_disables_effort_routing(self):
        os.environ["CLAUDE_CODE_EFFORT_LEVEL"] = "high"
        self.assertNotIn("effort", decide(LOOKUP)["updated"])

    def test_low_confidence_is_left_alone(self):
        rec = decide({"subagent_type": "general-purpose", "description": "Handle it", "prompt": "Take care of the thing."})
        self.assertEqual(rec["action"], "low-confidence")
        self.assertIsNone(rec["updated"])

    def test_plan_and_fork_are_never_routed(self):
        for t in ("Plan", "fork", "statusline-setup"):
            rec = decide(dict(LOOKUP, subagent_type=t))
            self.assertEqual(rec["action"], "skipped", t)
            self.assertIsNone(rec["updated"])

    def test_explore_can_be_left_alone(self):
        self.assertEqual(decide(dict(LOOKUP, subagent_type="Explore"))["action"], "filled")
        self.assertEqual(decide(dict(LOOKUP, subagent_type="Explore"), explore="leave")["action"], "skipped")

    def test_other_plugins_agents_are_left_alone(self):
        self.assertEqual(decide(dict(LOOKUP, subagent_type="someplugin:finder"))["action"], "pinned")
        self.assertEqual(decide(dict(LOOKUP, subagent_type="gearshift:scout"))["action"], "pinned")
        self.assertEqual(decide(dict(LOOKUP, subagent_type="never-heard-of-it"))["action"], "pinned")

    def test_user_agent_pinned_vs_unpinned(self):
        proj = tempfile.mkdtemp()
        try:
            d = os.path.join(proj, ".claude", "agents")
            os.makedirs(d)
            with open(os.path.join(d, "a.md"), "w") as f:
                f.write("---\nname: my-finder\ndescription: finds\nmodel: opus\n---\nbody\n")
            with open(os.path.join(d, "b.md"), "w") as f:
                f.write("---\nname: my-helper\ndescription: helps\nmodel: inherit\n---\nbody\n")
            with open(os.path.join(d, "c.md"), "w") as f:
                f.write("---\nname: my-effort\ndescription: helps\neffort: high\n---\nbody\n")
            self.assertEqual(decide(dict(LOOKUP, subagent_type="my-finder"), project_dir=proj)["action"], "pinned")
            rec = decide(dict(LOOKUP, subagent_type="my-helper"), project_dir=proj)
            self.assertEqual((rec["action"], rec["updated"]["model"]), ("filled", "haiku"))
            rec = decide(dict(LOOKUP, subagent_type="my-effort"), project_dir=proj)
            self.assertEqual(rec["updated"]["model"], "haiku")
            self.assertNotIn("effort", rec["updated"])   # the definition's own effort stands
        finally:
            shutil.rmtree(proj, ignore_errors=True)

    def test_existing_effort_is_kept(self):
        rec = decide(dict(LOOKUP, effort="high"))
        self.assertEqual(rec["updated"]["effort"], "high")
        self.assertEqual(rec["updated"]["model"], "haiku")


class HookProcess(unittest.TestCase):
    """Run the real launcher the way Claude Code does: JSON on stdin."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.env = dict(os.environ)
        self.env.update({"HOME": self.tmp, "USERPROFILE": self.tmp, "GEARSHIFT_HOME": os.path.join(self.tmp, "state"),
                         "XDG_CONFIG_HOME": os.path.join(self.tmp, ".config"), "GEARSHIFT_STOP_DELAY": "0"})
        for k in list(self.env):
            if k.startswith(("CLAUDE_PLUGIN_OPTION_", "GEARSHIFT_MODE", "GEARSHIFT_CEILING", "CLAUDE_CODE_EFFORT_LEVEL",
                             "CLAUDE_PROJECT_DIR", "ANTHROPIC_API_KEY", "ANTHROPIC_BASE_URL", "GEARSHIFT_JUDGE", "GEARSHIFT_API_KEY",
                             "GEARSHIFT_INSIDE_JUDGE", "GEARSHIFT_PROBE", "GEARSHIFT_HINT")):
                self.env.pop(k)
        self.transcript = os.path.join(self.tmp, "main.jsonl")
        with open(self.transcript, "w") as f:
            f.write(json.dumps({"type": "user", "version": "2.1.322", "message": {"role": "user", "content": "hi"}}) + "\n")
            f.write(json.dumps({"type": "assistant", "version": "2.1.322",
                                "message": {"id": "m1", "model": "claude-opus-5-5",
                                            "usage": {"input_tokens": 10, "output_tokens": 5}}}) + "\n")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def hook(self, event, payload, env=None, raw=None):
        e = dict(self.env)
        e.update(env or {})
        p = subprocess.run([sys.executable, LAUNCHER, "hook", event], input=raw if raw is not None else json.dumps(payload),
                           capture_output=True, text=True, env=e, cwd=self.tmp, timeout=30)
        return p

    def events(self):
        path = os.path.join(self.tmp, "state", "events.jsonl")
        if not os.path.exists(path):
            return []
        with open(path) as f:
            return [json.loads(x) for x in f if x.strip()]

    def pre(self, tool_input, **env):
        return self.hook("pre-agent", {"session_id": "s1", "transcript_path": self.transcript, "cwd": self.tmp,
                                       "hook_event_name": "PreToolUse", "tool_name": "Agent", "tool_use_id": "tu1",
                                       "tool_input": tool_input}, env=env)

    def test_pre_agent_output_shape(self):
        p = self.pre(LOOKUP)
        self.assertEqual(p.returncode, 0, p.stderr)
        out = json.loads(p.stdout)
        hs = out["hookSpecificOutput"]
        self.assertEqual(hs["hookEventName"], "PreToolUse")
        self.assertEqual(hs["permissionDecision"], "allow")
        want = dict(LOOKUP, model="haiku", effort="medium")
        want["prompt"] = LOOKUP["prompt"] + "\n\n" + HINT     # routed down, so it gets the escalate hint
        self.assertEqual(hs["updatedInput"], want)
        p = self.pre(LOOKUP, GEARSHIFT_HINT="0")
        self.assertEqual(json.loads(p.stdout)["hookSpecificOutput"]["updatedInput"],
                         dict(LOOKUP, model="haiku", effort="medium"))
        self.assertIn("haiku@medium", out["systemMessage"])
        ev = [e for e in self.events() if e["ev"] == "route"][0]
        self.assertEqual((ev["action"], ev["class"], ev["session_tier"]), ("filled", "lookup", "opus"))

    def _fake_api(self, reply, status=200):
        import threading
        from http.server import BaseHTTPRequestHandler, HTTPServer
        hits = []

        class H(BaseHTTPRequestHandler):
            def do_POST(self):
                hits.append(json.loads(self.rfile.read(int(self.headers["content-length"]))))
                out = json.dumps({"content": [{"type": "text", "text": reply}]}).encode()
                self.send_response(status)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(out)))
                self.end_headers()
                self.wfile.write(out)

            def log_message(self, *a):
                pass
        srv = HTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.server_close)
        self.addCleanup(srv.shutdown)
        env = {"GEARSHIFT_API_KEY": "sk-test", "ANTHROPIC_BASE_URL": "http://127.0.0.1:%d" % srv.server_port,
               "NO_PROXY": "127.0.0.1", "no_proxy": "127.0.0.1"}
        return hits, env

    def test_unknown_language_is_left_alone_without_a_judge(self):
        p = self.pre(GERMAN)
        self.assertEqual(p.stdout, "")
        self.assertEqual(self.events()[-1]["action"], "low-confidence")

    def test_judge_routes_what_the_keywords_cannot(self):
        hits, env = self._fake_api('{"class": "lookup", "changes_code": false}')
        p = self.pre(GERMAN, **env)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(json.loads(p.stdout)["hookSpecificOutput"]["updatedInput"]["model"], "haiku")
        ev = self.events()[-1]
        self.assertEqual((ev["action"], ev["class"], ev["judged"]), ("filled", "lookup", True))
        self.assertEqual(hits[0]["model"], "claude-haiku-5-5")
        # same prompt again: answered from the cache file, no second model call
        self.pre(GERMAN, **env)
        self.assertEqual(len(hits), 1)

    def test_judge_is_not_called_for_confident_calls(self):
        hits, env = self._fake_api('{"class": "design"}')
        p = self.pre(LOOKUP, **env)
        self.assertEqual(json.loads(p.stdout)["hookSpecificOutput"]["updatedInput"]["model"], "haiku")
        self.assertEqual(hits, [])

    def test_judge_failure_leaves_the_call_alone(self):
        hits, env = self._fake_api("{}", status=500)
        p = self.pre(GERMAN, **env)
        self.assertEqual((p.returncode, p.stdout), (0, ""))
        self.assertEqual(self.events()[-1]["action"], "low-confidence")
        env["ANTHROPIC_BASE_URL"] = "http://127.0.0.1:9"     # nothing listens here
        p = self.pre(GERMAN, **env)
        self.assertEqual((p.returncode, p.stdout), (0, ""))

    def test_judge_off_switch_and_project_file(self):
        hits, env = self._fake_api('{"class": "lookup"}')
        p = self.pre(GERMAN, GEARSHIFT_JUDGE="off", **env)
        self.assertEqual((p.stdout, hits), ("", []))
        # a repo you cloned must not be able to switch the judge on or point it elsewhere
        with open(os.path.join(self.tmp, ".gearshift.json"), "w") as f:
            json.dump({"judge": "api"}, f)
        p = self.pre(GERMAN, GEARSHIFT_JUDGE="off", CLAUDE_PROJECT_DIR=self.tmp, **env)
        self.assertEqual((p.stdout, hits), ("", []))

    def test_hooks_stay_out_of_the_judge_child_session(self):
        p = self.pre(LOOKUP, GEARSHIFT_INSIDE_JUDGE="1")
        self.assertEqual((p.returncode, p.stdout), (0, ""))
        self.assertEqual(self.events(), [])

    def post(self, text, tool_use_id="tu1", status="completed", **env):
        return self.hook("post-agent", {
            "session_id": "s1", "transcript_path": self.transcript, "cwd": self.tmp, "tool_name": "Agent",
            "tool_use_id": tool_use_id, "tool_input": {},
            "tool_response": {"status": status, "agentId": "a-" + tool_use_id,
                              "content": [{"type": "text", "text": text}]}}, env=env)

    def test_escalate_is_recorded_and_the_orchestrator_is_told(self):
        self.pre(LOOKUP)
        p = self.post("ESCALATE: the handlers are generated at runtime, grep is not enough")
        self.assertEqual(p.returncode, 0, p.stderr)
        ctx = json.loads(p.stdout)["hookSpecificOutput"]["additionalContext"]
        self.assertIn("haiku@medium", ctx)
        self.assertIn("model=haiku, effort=high", ctx)
        done = [e for e in self.events() if e["ev"] == "done"][-1]
        self.assertTrue(done["escalated"])
        self.assertIn("generated at runtime", done["escalate_reason"])

    def test_a_normal_answer_is_not_an_escalation(self):
        self.pre(LOOKUP)
        p = self.post("Found 14 endpoints. We may need to escalate: see notes.")
        self.assertEqual(p.stdout, "")
        self.assertNotIn("escalated", [e for e in self.events() if e["ev"] == "done"][-1])

    def test_same_task_again_starts_one_rung_up(self):
        self.pre(LOOKUP)
        self.post("ESCALATE: could not find them")
        again = dict(LOOKUP, prompt=LOOKUP["prompt"] + " Look harder, including generated routes.")
        p = self.hook("pre-agent", {"session_id": "s1", "transcript_path": self.transcript, "cwd": self.tmp,
                                    "tool_name": "Agent", "tool_use_id": "tu2", "tool_input": again})
        out = json.loads(p.stdout)
        self.assertEqual((out["hookSpecificOutput"]["updatedInput"]["model"],
                          out["hookSpecificOutput"]["updatedInput"]["effort"]), ("haiku", "high"))
        self.assertIn("fell short on haiku@medium", out["systemMessage"])
        ev = [e for e in self.events() if e["ev"] == "route"][-1]
        self.assertEqual((ev["retry_of"], ev["attempt"], ev["bumped_from"]), ("tu1", 2, "haiku@medium"))
        # and again: haiku@high failed too, so now it is sonnet
        self.post("ESCALATE: still nothing", tool_use_id="tu2")
        p = self.hook("pre-agent", {"session_id": "s1", "transcript_path": self.transcript, "cwd": self.tmp,
                                    "tool_name": "Agent", "tool_use_id": "tu3", "tool_input": again})
        self.assertEqual(json.loads(p.stdout)["hookSpecificOutput"]["updatedInput"]["model"], "sonnet")

    def test_repeat_without_a_failure_is_routed_as_usual(self):
        self.pre(LOOKUP)
        self.post("Found 14 endpoints.")
        p = self.hook("pre-agent", {"session_id": "s1", "transcript_path": self.transcript, "cwd": self.tmp,
                                    "tool_name": "Agent", "tool_use_id": "tu2", "tool_input": LOOKUP})
        up = json.loads(p.stdout)["hookSpecificOutput"]["updatedInput"]
        self.assertEqual((up["model"], up["effort"]), ("haiku", "medium"))

    def test_retry_never_climbs_past_the_session_model(self):
        with open(self.transcript, "w") as f:      # a Sonnet session this time
            f.write(json.dumps({"type": "assistant", "version": "2.1.322",
                                "message": {"id": "m1", "model": "claude-sonnet-5-5",
                                            "usage": {"input_tokens": 10, "output_tokens": 5}}}) + "\n")
        edit = {"subagent_type": "general-purpose", "description": "Fix typo",
                "prompt": "Fix the typo in the error message in login.py."}
        self.pre(edit)
        p = self.post("ESCALATE: the message is built in three places")
        self.assertIn("model=sonnet, effort=high", json.loads(p.stdout)["hookSpecificOutput"]["additionalContext"])
        p = self.hook("pre-agent", {"session_id": "s1", "transcript_path": self.transcript, "cwd": self.tmp,
                                    "tool_name": "Agent", "tool_use_id": "tu2", "tool_input": edit})
        up = json.loads(p.stdout)["hookSpecificOutput"]["updatedInput"]
        self.assertEqual((up.get("model"), up["effort"]), (None, "high"))    # stays on the session model
        # sonnet@high was the top of a Sonnet session: nothing left to climb to
        p = self.post("ESCALATE: still wrong", tool_use_id="tu2")
        self.assertIn("main session", json.loads(p.stdout)["hookSpecificOutput"]["additionalContext"])
        p = self.hook("pre-agent", {"session_id": "s1", "transcript_path": self.transcript, "cwd": self.tmp,
                                    "tool_name": "Agent", "tool_use_id": "tu3", "tool_input": edit})
        if p.stdout:
            self.assertNotIn(json.loads(p.stdout)["hookSpecificOutput"]["updatedInput"].get("model"),
                             ("opus", "fable"))

    def test_probe_in_the_hook(self):
        from gearshift.outcomes import sampled
        review = {"subagent_type": "general-purpose", "description": "Review the diff",
                  "prompt": "Review this pull request for logic errors and report what you find. Do not change any files."}
        yes = next("tu%d" % i for i in range(100) if sampled("tu%d" % i, 0.5))
        no = next("tu%d" % i for i in range(100) if not sampled("tu%d" % i, 0.5))
        out = {}
        for tid in (yes, no):
            p = self.hook("pre-agent", {"session_id": "s-" + tid, "transcript_path": self.transcript, "cwd": self.tmp,
                                        "tool_name": "Agent", "tool_use_id": tid, "tool_input": review},
                          env={"GEARSHIFT_PROBE": "0.5"})
            out[tid] = json.loads(p.stdout)
        up = out[yes]["hookSpecificOutput"]["updatedInput"]
        self.assertEqual((up["model"], up["effort"]), ("sonnet", "medium"))
        self.assertIn(HINT, up["prompt"])
        self.assertIn("probe: one step below sonnet@high", out[yes]["systemMessage"])
        up = out[no]["hookSpecificOutput"]["updatedInput"]
        self.assertEqual((up["model"], up["effort"]), ("sonnet", "high"))
        probes = [e for e in self.events() if e["ev"] == "route" and e.get("probe_from")]
        self.assertEqual([e["tool_use_id"] for e in probes], [yes])

    def test_pinned_agent_escalation_climbs_from_its_own_model(self):
        scout = {"subagent_type": "gearshift:scout", "description": "Run tests", "prompt": "Run the test suite and report."}
        self.hook("pre-agent", {"session_id": "s1", "transcript_path": self.transcript, "cwd": self.tmp,
                                "tool_name": "Agent", "tool_use_id": "tu1", "tool_input": scout},
                  env={"CLAUDE_PLUGIN_ROOT": ROOT})
        ev = [e for e in self.events() if e["ev"] == "route"][-1]
        self.assertEqual((ev["action"], ev.get("pinned_model")), ("pinned", "haiku"))
        p = self.post("ESCALATE: the failure is in generated code I cannot read")
        ctx = json.loads(p.stdout)["hookSpecificOutput"]["additionalContext"]
        self.assertIn("ran on haiku", ctx)
        self.assertNotIn("opus@high", ctx)

    def test_shadow_mode_records_outcomes_but_says_nothing(self):
        self.pre(LOOKUP, GEARSHIFT_MODE="shadow")
        p = self.post("ESCALATE: too hard", GEARSHIFT_MODE="shadow")
        self.assertEqual(p.stdout, "")
        self.assertTrue([e for e in self.events() if e["ev"] == "done"][-1]["escalated"])

    def test_task_alias_matches_too(self):
        p = self.hook("pre-agent", {"session_id": "s1", "transcript_path": self.transcript, "tool_name": "Task",
                                    "tool_input": LOOKUP})
        self.assertIn("updatedInput", p.stdout)

    def test_other_tools_are_ignored(self):
        p = self.hook("pre-agent", {"session_id": "s1", "tool_name": "Bash", "tool_input": {"command": "ls"}})
        self.assertEqual((p.returncode, p.stdout), (0, ""))

    def test_mode_off_and_shadow(self):
        p = self.pre(LOOKUP, GEARSHIFT_MODE="off")
        self.assertEqual(p.stdout, "")
        self.assertEqual(self.events(), [])
        p = self.pre(LOOKUP, GEARSHIFT_MODE="shadow")
        self.assertEqual(p.stdout, "")
        self.assertEqual(self.events()[-1]["action"], "shadow")

    def test_plugin_option_env_is_honoured(self):
        p = self.pre(dict(LOOKUP, model="opus"), CLAUDE_PLUGIN_OPTION_MODE="strict")
        self.assertEqual(json.loads(p.stdout)["hookSpecificOutput"]["updatedInput"]["model"], "haiku")

    def test_announce_and_permission_options(self):
        p = self.pre(LOOKUP, GEARSHIFT_ANNOUNCE="false", GEARSHIFT_PERMISSION="none")
        out = json.loads(p.stdout)
        self.assertNotIn("systemMessage", out)
        self.assertNotIn("permissionDecision", out["hookSpecificOutput"])

    def test_project_file_cannot_raise_the_ceiling_or_force_strict(self):
        with open(os.path.join(self.tmp, ".gearshift.json"), "w") as f:
            json.dump({"mode": "strict", "ceiling": "fable",
                       "policy": {"classes": {"lookup": {"tier": "fable", "effort": "xhigh"}}}}, f)
        p = self.pre(dict(LOOKUP, model="haiku"))
        self.assertEqual(p.stdout, "", "a repo-supplied config must not make calls more expensive")
        p = self.pre(LOOKUP)   # policy says fable, but the session is opus and ceiling=session
        upd = json.loads(p.stdout)["hookSpecificOutput"]["updatedInput"] if p.stdout else {}
        self.assertNotEqual(upd.get("model"), "fable")

    def test_project_file_can_turn_routing_off(self):
        with open(os.path.join(self.tmp, ".gearshift.json"), "w") as f:
            json.dump({"mode": "off"}, f)
        self.assertEqual(self.pre(LOOKUP).stdout, "")

    def test_fails_open_on_bad_input(self):
        for raw in ("not json", "", "[]", "null", '{"tool_name": "Agent"}',
                    '{"tool_name": "Agent", "tool_input": "str"}', '{"tool_name": "Agent", "tool_input": {"prompt": 7}}'):
            for event in ("pre-agent", "post-agent", "subagent-stop", "user-prompt", "session-start",
                          "pre-model-switch", "post-model-switch", "no-such-event"):
                p = self.hook(event, None, raw=raw)
                self.assertEqual(p.returncode, 0, (event, raw, p.stderr))
                self.assertEqual(p.stderr, "", (event, raw))
                if p.stdout:
                    json.loads(p.stdout)

    def test_fails_open_when_state_dir_is_unwritable(self):
        blocker = os.path.join(self.tmp, "blocker")
        open(blocker, "w").close()
        p = self.pre(LOOKUP, GEARSHIFT_HOME=os.path.join(blocker, "state"))
        self.assertEqual(p.returncode, 0)
        self.assertIn("updatedInput", p.stdout)   # routing still works without a ledger

    def test_fails_open_on_broken_policy_override(self):
        bad = os.path.join(self.tmp, "bad.json")
        with open(bad, "w") as f:
            f.write('{"classes": {"lookup": {"tier": "nope", "effort": "low"}}}')
        p = self.pre(LOOKUP, GEARSHIFT_POLICY=bad)
        self.assertEqual((p.returncode, p.stdout, p.stderr), (0, "", ""))

    def test_session_start_brief_and_model_memory(self):
        p = self.hook("session-start", {"session_id": "s9", "source": "startup", "model": "claude-sonnet-5-5"})
        ctx = json.loads(p.stdout)["hookSpecificOutput"]
        self.assertEqual(ctx["hookEventName"], "SessionStart")
        self.assertIn("gearshift:scout", ctx["additionalContext"])
        # no transcript given: the tier must come from what SessionStart saved
        p = self.hook("pre-agent", {"session_id": "s9", "tool_name": "Agent", "tool_input": BUILD})
        ev = [e for e in self.events() if e["ev"] == "route"][-1]
        self.assertEqual(ev["session_tier"], "sonnet")
        p = self.hook("session-start", {"session_id": "s9", "source": "startup"}, env={"GEARSHIFT_BRIEF": "0"})
        self.assertEqual(p.stdout, "")

    def test_model_switch_hooks(self):
        self.hook("session-start", {"session_id": "s3", "model": "claude-opus-5-5"})
        p = self.hook("pre-model-switch", {"session_id": "s3", "from_model": "claude-opus-5-5", "to_model": "claude-sonnet-5-5",
                                           "context_tokens": 182340, "prompt_cache_warm": True,
                                           "estimated_cache_write_usd": 0.4559})
        msg = json.loads(p.stdout)["systemMessage"]
        self.assertIn("182,340", msg)
        self.assertIn("$0.46", msg)
        self.assertNotIn("hookSpecificOutput", json.loads(p.stdout), "must never block or auto-approve a switch")
        cold = self.hook("pre-model-switch", {"session_id": "s3", "to_model": "x", "context_tokens": 182340,
                                              "prompt_cache_warm": False})
        self.assertEqual(cold.stdout, "")
        small = self.hook("pre-model-switch", {"session_id": "s3", "to_model": "x", "context_tokens": 900,
                                               "prompt_cache_warm": True})
        self.assertEqual(small.stdout, "")
        self.hook("post-model-switch", {"session_id": "s3", "to_model": "claude-sonnet-5-5", "source": "command"})
        self.hook("pre-agent", {"session_id": "s3", "tool_name": "Agent", "tool_input": BUILD})
        self.assertEqual([e for e in self.events() if e["ev"] == "route"][-1]["session_tier"], "sonnet")

    def test_user_prompt_advice_once(self):
        self.hook("session-start", {"session_id": "s4", "model": "claude-haiku-5-5"})
        big = {"session_id": "s4", "prompt": "Plan the migration of our entire monolith from Django to FastAPI."}
        p = self.hook("user-prompt", big)
        self.assertIn("/model opus", json.loads(p.stdout)["systemMessage"])
        self.assertEqual(self.hook("user-prompt", big).stdout, "", "advice is given once per session")
        self.assertEqual(self.hook("user-prompt", {"session_id": "s4", "prompt": "ok"}).stdout, "")
        self.assertEqual(self.hook("user-prompt", {"session_id": "s4", "prompt": "/model opus please do it now"}).stdout, "")

    def test_too_big_needs_a_streak(self):
        self.hook("session-start", {"session_id": "s5", "model": "claude-fable-5-2"})
        small = {"session_id": "s5", "prompt": "Fix the typo in the error message in src/errors.ts."}
        outs = [self.hook("user-prompt", small).stdout for _ in range(6)]
        self.assertEqual(outs[:4], [""] * 4)
        self.assertIn("Sonnet", json.loads(outs[4])["systemMessage"])
        self.assertEqual(outs[5], "")

    def test_advice_can_be_disabled(self):
        self.hook("session-start", {"session_id": "s6", "model": "claude-haiku-5-5"})
        p = self.hook("user-prompt", {"session_id": "s6", "prompt": "Plan the migration of our entire monolith to FastAPI."},
                      env={"GEARSHIFT_ADVISE": "false"})
        self.assertEqual(p.stdout, "")

    def test_subagent_stop_totals_the_whole_transcript(self):
        sub = os.path.join(self.tmp, "agent-a1.jsonl")
        lines = [
            {"type": "user", "message": {"role": "user", "content": "go"}},
            # streaming: same message id twice, the later line has the final counts
            {"type": "assistant", "message": {"id": "r1", "model": "claude-haiku-5-5",
                                              "usage": {"input_tokens": 1000, "output_tokens": 10}}},
            {"type": "assistant", "message": {"id": "r1", "model": "claude-haiku-5-5",
                                              "usage": {"input_tokens": 1000, "output_tokens": 400}}},
            {"type": "assistant", "message": {"id": "r2", "model": "claude-haiku-5-5",
                                              "usage": {"input_tokens": 200, "output_tokens": 600, "cache_read_input_tokens": 5000,
                                                        "cache_creation_input_tokens": 300}}},
            {"type": "assistant", "isApiErrorMessage": True, "message": {"id": "e1", "model": "<synthetic>",
                                                                         "usage": {"input_tokens": 0, "output_tokens": 0}}},
        ]
        with open(sub, "w") as f:
            f.write("\n".join(json.dumps(x) for x in lines) + "\n{broken json\n")
        self.hook("session-start", {"session_id": "s7", "model": "claude-opus-5-5"})
        p = self.hook("subagent-stop", {"session_id": "s7", "agent_id": "a1", "agent_type": "Explore",
                                        "agent_transcript_path": sub})
        self.assertEqual((p.returncode, p.stdout), (0, ""))
        ev = [e for e in self.events() if e["ev"] == "usage"][0]
        self.assertEqual(ev["requests"], 2)
        u = ev["by_model"]["claude-haiku-5-5"]
        self.assertEqual((u["input_tokens"], u["output_tokens"], u["cache_read_input_tokens"], u["cache_write_5m_tokens"]),
                         (1200, 1000, 5000, 300))
        haiku = (1200 * 0.10 + 1000 * 0.50 + 5000 * 0.01 + 300 * 0.125) / 1e6
        opus = (1200 * 4 + 1000 * 20 + 5000 * 0.20 + 300 * 5) / 1e6
        self.assertAlmostEqual(ev["cost_usd"], haiku, places=6)
        self.assertAlmostEqual(ev["session_rate_cost_usd"], opus, places=6)

        rep = subprocess.run([sys.executable, LAUNCHER, "report", "--json"], capture_output=True, text=True, env=self.env)
        data = json.loads(rep.stdout)
        self.assertEqual(data["subagents_measured"], 1)
        self.assertEqual(data["tiers"]["haiku"]["runs"], 1)
        self.assertAlmostEqual(data["spend_usd"], round(haiku, 4))
        text = subprocess.run([sys.executable, LAUNCHER, "report"], capture_output=True, text=True, env=self.env).stdout
        self.assertIn("comparison aid, not a measurement", text)
        haiku_line = [ln for ln in text.splitlines() if ln.strip().startswith("haiku")][0]
        self.assertTrue(haiku_line.rstrip().endswith("Explore"), haiku_line)

    def test_subagent_stop_with_missing_transcript(self):
        p = self.hook("subagent-stop", {"session_id": "s8", "agent_id": "zz", "agent_transcript_path": "/no/such/file"})
        self.assertEqual((p.returncode, p.stdout, p.stderr), (0, "", ""))

    def test_post_agent_records_resolved_model(self):
        self.hook("post-agent", {"session_id": "s1", "tool_name": "Agent", "tool_use_id": "tu1",
                                 "tool_response": {"status": "completed", "agentId": "a1", "resolvedModel": "claude-haiku-5-5",
                                                   "totalDurationMs": 1200, "totalToolUseCount": 3}})
        ev = [e for e in self.events() if e["ev"] == "done"][0]
        self.assertEqual((ev["resolved_model"], ev["agent_id"]), ("claude-haiku-5-5", "a1"))

    @unittest.skipIf(shutil.which("sh") is None, "no POSIX shell")
    def test_shell_launcher(self):
        p = subprocess.run(["sh", SH_LAUNCHER, "hook", "pre-agent"], input=json.dumps(
            {"session_id": "s1", "transcript_path": self.transcript, "tool_name": "Agent", "tool_input": LOOKUP}),
            capture_output=True, text=True, env=self.env, timeout=30)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("updatedInput", p.stdout)

    @unittest.skipIf(shutil.which("sh") is None, "no POSIX shell")
    def test_shell_launcher_fails_open_without_python(self):
        env = dict(self.env, PATH="/nonexistent", GEARSHIFT_PYTHON="")
        sh = shutil.which("sh")
        p = subprocess.run([sh, SH_LAUNCHER, "hook", "pre-agent"], input="{}", capture_output=True, text=True, env=env)
        self.assertEqual((p.returncode, p.stdout), (0, ""))
        p = subprocess.run([sh, SH_LAUNCHER, "doctor"], input="", capture_output=True, text=True, env=env)
        self.assertEqual(p.returncode, 1)
        self.assertIn("Python 3.9", p.stderr)


if __name__ == "__main__":
    unittest.main()
