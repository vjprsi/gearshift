import unittest

from tests.helpers import clean_router

from gearshift.execute import CallResult, run, run_batch

USAGE = {"input_tokens": 1000, "output_tokens": 1000}


class FakeModel:
    """Passes once the (tier, effort) rung is at or above ``need``."""

    ORDER = ["haiku@low", "haiku@medium", "haiku@high", "sonnet@medium", "sonnet@high",
             "opus@medium", "opus@high", "opus@xhigh", "fable@high", "fable@xhigh"]

    def __init__(self, need):
        self.need = need
        self.calls = []

    def __call__(self, model, effort, prompt, feedback=None):
        tier = model.split("-")[1]
        rung = "%s@%s" % (tier, effort)
        self.calls.append((rung, feedback))
        ok = self.ORDER.index(rung) >= self.ORDER.index(self.need)
        return CallResult("RIGHT" if ok else "WRONG", USAGE)


def verify(out):
    return (out == "RIGHT", None if out == "RIGHT" else "expected RIGHT")


TASK = "Fix the typo in the error message in src/errors.ts."   # routes to sonnet@medium


class Run(unittest.TestCase):
    def setUp(self):
        self.router = clean_router()

    def test_passes_first_time(self):
        m = FakeModel("sonnet@medium")
        r = run(TASK, m, verify, router=self.router)
        self.assertTrue(r.ok)
        self.assertEqual([c[0] for c in m.calls], ["sonnet@medium"])
        self.assertEqual(r.stopped, "passed")
        self.assertAlmostEqual(r.cost_usd, (1000 * 2 + 1000 * 10) / 1e6)

    def test_climbs_effort_then_model_and_passes_feedback(self):
        m = FakeModel("opus@medium")
        r = run(TASK, m, verify, router=self.router)
        self.assertTrue(r.ok)
        self.assertEqual([c[0] for c in m.calls], ["sonnet@medium", "sonnet@high", "opus@medium"])
        self.assertEqual([c[1] for c in m.calls], [None, "expected RIGHT", "expected RIGHT"])
        self.assertEqual(len(r.attempts), 3)
        self.assertAlmostEqual(r.cost_usd, (2 * 12000 + 24000) / 1e6)

    def test_gives_up_after_max_attempts(self):
        m = FakeModel("fable@xhigh")
        r = run(TASK, m, verify, router=self.router, max_attempts=2)
        self.assertFalse(r.ok)
        self.assertEqual(r.stopped, "exhausted")
        self.assertEqual(len(m.calls), 2)

    def test_budget_stops_escalation(self):
        m = FakeModel("fable@xhigh")
        r = run(TASK, m, verify, router=self.router, max_attempts=5, budget_usd=0.012)
        self.assertEqual(r.stopped, "budget")
        self.assertEqual(len(m.calls), 1)

    def test_without_verify_exactly_one_attempt(self):
        m = FakeModel("fable@xhigh")
        r = run(TASK, m, router=self.router)
        self.assertEqual(len(m.calls), 1)
        self.assertTrue(r.ok)
        self.assertEqual(r.stopped, "unverified")

    def test_ceiling_limits_the_climb(self):
        m = FakeModel("fable@xhigh")
        run(TASK, m, verify, router=self.router, max_attempts=10, ceiling="sonnet")
        self.assertEqual([c[0] for c in m.calls], ["sonnet@medium", "sonnet@high"])

    def test_call_errors_propagate(self):
        def boom(**kw):
            raise ConnectionError("down")
        with self.assertRaises(ConnectionError):
            run(TASK, boom, verify, router=self.router)

    def test_accepts_plain_string_and_tuple_results(self):
        r = run(TASK, lambda **kw: "RIGHT", verify, router=self.router)
        self.assertTrue(r.ok)
        self.assertFalse(r.cost_known)
        r = run(TASK, lambda **kw: ("RIGHT", USAGE), verify, router=self.router)
        self.assertTrue(r.cost_known)

    def test_boolean_verify(self):
        r = run(TASK, lambda **kw: "RIGHT", lambda out: out == "RIGHT", router=self.router)
        self.assertTrue(r.ok)


class Batch(unittest.TestCase):
    def test_cheap_first_reruns_only_failures(self):
        calls = []

        def call(model, effort, prompt, feedback=None):
            calls.append((prompt, effort))
            hard = prompt.startswith("hard")
            return CallResult("RIGHT" if (effort == "high" or not hard) else "WRONG", USAGE)

        items = ["easy-1", "hard-1", "easy-2", "hard-2", "easy-3"]
        b = run_batch(items, call, lambda item, out: out == "RIGHT", router=clean_router())
        self.assertEqual((b.total, b.passed, b.first_pass), (5, 5, 3))
        self.assertEqual(len(calls), 7)
        self.assertEqual(sorted(p for p, e in calls if e == "high"), ["hard-1", "hard-2"])
        per_call = (1000 * 4 + 1000 * 20) / 1e6
        self.assertAlmostEqual(b.cost_usd, 7 * per_call)
        self.assertAlmostEqual(b.cost_per_finished_task, 7 * per_call / 5)

    def test_cost_per_finished_counts_failed_spend(self):
        b = run_batch(["a", "b"], lambda **kw: CallResult("WRONG" if kw["prompt"] == "b" else "RIGHT", USAGE),
                      lambda item, out: out == "RIGHT", first=("haiku", "low"), retry=None, router=clean_router())
        self.assertEqual(b.passed, 1)
        self.assertAlmostEqual(b.cost_per_finished_task, b.cost_usd)
        self.assertEqual(b.summary()["pass_rate"], 0.5)

    def test_nothing_passes(self):
        b = run_batch(["a"], lambda **kw: "WRONG", lambda item, out: False, router=clean_router())
        self.assertIsNone(b.cost_per_finished_task)

    def test_routed_batch(self):
        seen = []

        def call(model, effort, prompt, feedback=None):
            seen.append(model)
            return CallResult("RIGHT", USAGE)
        run_batch(["Extract the dates from this paragraph as JSON.", "Design the architecture for a ledger service."],
                  call, lambda i, o: True, first=None, retry=None, router=clean_router())
        self.assertEqual(seen, ["claude-haiku-5-5", "claude-opus-5-5"])


if __name__ == "__main__":
    unittest.main()
