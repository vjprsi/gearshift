import inspect
import unittest

from tests.helpers import clean_router

from gearshift.anthropic_adapter import build_request, make_call, supported_effort
from gearshift.execute import run
from gearshift.judge import make_judge
from gearshift.policy import load_policy
from gearshift.router import Router


class FakeUsage:
    input_tokens = 100
    output_tokens = 50
    cache_read_input_tokens = 0
    cache_creation_input_tokens = 0
    cache_creation = None


class FakeBlock:
    def __init__(self, type_, text=""):
        self.type, self.text = type_, text


class FakeMessage:
    def __init__(self, text):
        self.content = [FakeBlock("thinking"), FakeBlock("text", text)]
        self.usage = FakeUsage()


class FakeStream:
    def __init__(self, text):
        self._text = text

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def get_final_message(self):
        return FakeMessage(self._text)


class FakeClient:
    def __init__(self, text="42"):
        self.requests = []
        self.messages = self
        self._text = text

    def stream(self, **kwargs):
        self.requests.append(kwargs)
        return FakeStream(self._text)


class Adapter(unittest.TestCase):
    def test_request_shape(self):
        req = build_request("claude-opus-5-5", "high", "hello")
        self.assertEqual(req["model"], "claude-opus-5-5")
        self.assertEqual(req["output_config"], {"effort": "high"})
        self.assertEqual(req["messages"], [{"role": "user", "content": "hello"}])
        self.assertGreaterEqual(req["max_tokens"], 32000)
        self.assertNotIn("thinking", req, "thinking is adaptive on current models; the adapter must not set it")

    def test_feedback_reaches_the_next_attempt(self):
        req = build_request("claude-opus-5-5", "high", "hello", "expected 42")
        self.assertIn("expected 42", req["messages"][0]["content"])
        self.assertIn("hello", req["messages"][0]["content"])

    def test_effort_is_clamped_or_dropped_for_older_models(self):
        self.assertEqual(supported_effort("claude-opus-4-6", "xhigh"), "high")
        self.assertIsNone(supported_effort("claude-haiku-4-5", "low"))
        self.assertNotIn("output_config", build_request("claude-haiku-4-5", "low", "x"))
        self.assertEqual(supported_effort("claude-fable-5-2", "xhigh"), "xhigh")

    def test_end_to_end_with_fake_client(self):
        client = FakeClient("the answer is 42")
        r = run("Extract the number from: forty-two (42). Reply as JSON.", make_call(client, system="be brief"),
                lambda out: "42" in out, router=clean_router())
        self.assertTrue(r.ok)
        self.assertEqual(r.output, "the answer is 42")   # thinking blocks are not part of the text
        req = client.requests[0]
        self.assertEqual((req["model"], req["output_config"]["effort"], req["system"]),
                         ("claude-haiku-5-5", "low", "be brief"))
        self.assertAlmostEqual(r.cost_usd, (100 * 0.10 + 50 * 0.50) / 1e6)

    def test_request_keys_exist_in_the_real_sdk(self):
        try:
            from anthropic.resources.messages import Messages
        except ImportError:
            self.skipTest("anthropic SDK not installed")
        params = inspect.signature(Messages.stream).parameters
        req = build_request("claude-opus-5-5", "high", "hello", system="s")
        for key in req:
            self.assertIn(key, params, "the SDK's messages.stream has no %r parameter" % key)
        from anthropic.types import OutputConfigParam
        self.assertIn("effort", OutputConfigParam.__annotations__)
        for level in ("low", "medium", "high", "xhigh", "max"):
            self.assertIn(level, str(OutputConfigParam.__annotations__["effort"]))


class Judge(unittest.TestCase):
    def test_judge_prompt_and_parse(self):
        client = FakeClient('{"class": "extract", "changes_code": false, "high_stakes": true}')
        judge = make_judge(make_call(client), policy=load_policy(use_user_files=False))
        v = judge("Sortiere diese Tickets nach Bereich")
        self.assertEqual((v["class"], v["flags"]), ("extract", ["stakes"]))
        req = client.requests[0]
        self.assertEqual((req["model"], req["output_config"]["effort"]), ("claude-haiku-5-5", "low"))
        body = req["messages"][0]["content"]
        self.assertIn("- lookup:", body)
        self.assertIn("Sortiere diese Tickets", body)
        self.assertNotIn("- unsure:", body)

    def test_judge_accepts_a_bare_class_name(self):
        judge = make_judge(make_call(FakeClient("Class: extract")), policy=load_policy(use_user_files=False))
        self.assertEqual(judge("Sortiere diese Tickets")["class"], "extract")

    def test_judge_safety_rules(self):
        pol = load_policy(use_user_files=False)
        v = make_judge(make_call(FakeClient('{"class":"lookup","changes_code":true}')), policy=pol)("x1")
        self.assertEqual(v["class"], "edit")
        v = make_judge(make_call(FakeClient('{"class":"edit","changes_code":true,"hard_bug":true}')), policy=pol)("x2")
        self.assertEqual(v["class"], "build")
        v = make_judge(make_call(FakeClient('{"class":"unsure"}')), policy=pol)("x3")
        self.assertIsNone(v)

    def test_judge_cache(self):
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "judge_cache.json"
            client = FakeClient('{"class": "review"}')
            pol = load_policy(use_user_files=False)
            judge = make_judge(make_call(client), policy=pol, cache_path=path)
            judge("Prüfe diesen Pull Request"); judge("Prüfe diesen Pull Request")
            self.assertEqual(len(client.requests), 1)
            again = make_judge(make_call(client), policy=pol, cache_path=path)
            self.assertEqual(again("Prüfe diesen Pull Request")["class"], "review")
            self.assertEqual(len(client.requests), 1, "second process must read the cache file")

    def test_api_transport_request_shape(self):
        import json, threading
        from http.server import BaseHTTPRequestHandler, HTTPServer
        from unittest import mock
        from gearshift.judge import api_transport
        seen = {}

        class H(BaseHTTPRequestHandler):
            def do_POST(self):
                seen["path"] = self.path
                seen["key"] = self.headers.get("x-api-key")
                seen["body"] = json.loads(self.rfile.read(int(self.headers["content-length"])))
                out = json.dumps({"content": [{"type": "thinking", "thinking": "hm"},
                                              {"type": "text", "text": '{"class": "lookup"}'}]}).encode()
                self.send_response(200); self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(out))); self.end_headers(); self.wfile.write(out)

            def log_message(self, *a):
                pass
        srv = HTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            env = {"ANTHROPIC_BASE_URL": "http://127.0.0.1:%d" % srv.server_port, "ANTHROPIC_API_KEY": "sk-test",
                   "NO_PROXY": "127.0.0.1", "no_proxy": "127.0.0.1"}
            with mock.patch.dict("os.environ", env):
                judge = make_judge(api_transport(), policy=load_policy(use_user_files=False))
                self.assertEqual(judge("Finde alle Aufrufer")["class"], "lookup")
        finally:
            srv.shutdown(); srv.server_close()
        self.assertEqual((seen["path"], seen["key"]), ("/v1/messages", "sk-test"))
        self.assertEqual(seen["body"]["model"], "claude-haiku-5-5")
        self.assertEqual(seen["body"]["output_config"], {"effort": "low"})

    def test_judge_from_config(self):
        from unittest import mock
        from gearshift.judge import judge_from_config
        with mock.patch.dict("os.environ", {}, clear=True):
            self.assertIsNone(judge_from_config("auto"))
            self.assertIsNone(judge_from_config("api"))
            self.assertIsNone(judge_from_config("off"))
        with mock.patch.dict("os.environ", {"ANTHROPIC_API_KEY": "sk-test"}, clear=True):
            self.assertIsNotNone(judge_from_config("auto"))
            self.assertIsNone(judge_from_config("off"))
        with mock.patch.dict("os.environ", {"ANTHROPIC_API_KEY": "sk-test", "GEARSHIFT_INSIDE_JUDGE": "1"}, clear=True):
            self.assertIsNone(judge_from_config("auto"), "no judge inside a judge call")

    @unittest.skipIf(__import__("os").name == "nt", "fake binary is a shell script")
    def test_claude_cli_transport(self):
        import os, stat, tempfile
        from gearshift.judge import claude_cli_transport
        with tempfile.TemporaryDirectory() as tmp:
            fake = os.path.join(tmp, "claude")
            with open(fake, "w") as f:
                f.write('#!/bin/sh\ncat > "%s/stdin.txt"\necho "$@ inside=$GEARSHIFT_INSIDE_JUDGE" > "%s/args.txt"\n'
                        'echo \'{"class": "design"}\'\n' % (tmp, tmp))
            os.chmod(fake, os.stat(fake).st_mode | stat.S_IEXEC)
            judge = make_judge(claude_cli_transport(binary=fake), policy=load_policy(use_user_files=False))
            self.assertEqual(judge("Entwirf die Architektur")["class"], "design")
            with open(os.path.join(tmp, "stdin.txt")) as f:
                self.assertIn("Entwirf die Architektur", f.read())
            with open(os.path.join(tmp, "args.txt")) as f:
                self.assertEqual(f.read().strip(), "-p --model haiku inside=1")

    def test_judge_returns_none_on_nonsense(self):
        judge = make_judge(make_call(FakeClient("I cannot tell")), policy=load_policy(use_user_files=False))
        self.assertIsNone(judge("???"))

    def test_router_with_judge(self):
        pol = load_policy(use_user_files=False)
        r = Router(pol, judge=make_judge(make_call(FakeClient("build")), policy=pol))
        d = r.route("Implementiere die Benachrichtigungsfunktion über mehrere Dateien")
        self.assertEqual((d.task_class, d.tier), ("build", "opus"))


if __name__ == "__main__":
    unittest.main()
