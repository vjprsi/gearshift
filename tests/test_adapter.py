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
        client = FakeClient("Class: extract")
        judge = make_judge(make_call(client), policy=load_policy(use_user_files=False))
        self.assertEqual(judge("Clasifica estos tickets por area"), "extract")
        req = client.requests[0]
        self.assertEqual((req["model"], req["output_config"]["effort"]), ("claude-haiku-5-5", "low"))
        self.assertIn("- lookup:", req["messages"][0]["content"])
        self.assertNotIn("unsure", req["messages"][0]["content"].split("Task:")[0].replace("unsure of", ""))

    def test_judge_returns_none_on_nonsense(self):
        judge = make_judge(make_call(FakeClient("I cannot tell")), policy=load_policy(use_user_files=False))
        self.assertIsNone(judge("???"))

    def test_router_with_judge(self):
        pol = load_policy(use_user_files=False)
        r = Router(pol, judge=make_judge(make_call(FakeClient("build")), policy=pol))
        d = r.route("Implementa la funcionalidad de notificaciones en varios archivos")
        self.assertEqual((d.task_class, d.tier), ("build", "opus"))


if __name__ == "__main__":
    unittest.main()
