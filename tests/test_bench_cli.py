import json
import os
import subprocess
import sys
import tempfile
import unittest

from tests.helpers import ROOT

from gearshift.bench import check_output, format_table, run_bench
from gearshift.execute import CallResult

LAUNCHER = os.path.join(ROOT, "bin", "gearshift.py")
TASKS = os.path.join(ROOT, "examples", "bench_tasks.jsonl")


def cli(*args, stdin=None):
    env = dict(os.environ, GEARSHIFT_HOME=tempfile.mkdtemp(), XDG_CONFIG_HOME=tempfile.mkdtemp())
    return subprocess.run([sys.executable, LAUNCHER] + list(args), input=stdin, capture_output=True, text=True, env=env)


class Checks(unittest.TestCase):
    def test_check_types(self):
        self.assertTrue(check_output({"check": {"type": "contains", "value": "Bogota"}}, "shipped to bogota")[0])
        self.assertFalse(check_output({"check": {"type": "equals", "value": "A-1"}}, "A-2")[0])
        self.assertTrue(check_output({"check": {"type": "regex", "value": r"^\d{3}$"}}, "123")[0])
        self.assertTrue(check_output({"check": {"type": "json_equals", "value": {"a": 1}}}, 'Sure: {"a": 1}')[0])
        ok, why = check_output({"check": {"type": "json_equals", "value": {"a": 1}}}, "nope")
        self.assertFalse(ok)
        self.assertIn("JSON", why)
        self.assertTrue(check_output({"check": {"type": "command", "value": "grep -q hello"}}, "hello world")[0])
        self.assertFalse(check_output({"check": {"type": "command", "value": "grep -q hello"}}, "bye")[0])
        with self.assertRaises(ValueError):
            check_output({"id": "x", "check": {"type": "vibes"}}, "")


class Bench(unittest.TestCase):
    def test_simulated_run_is_labelled_and_deterministic(self):
        a = run_bench(TASKS, configs=["haiku@medium", "opus@medium", "router", "cascade", "cheap-first:opus"], simulate=True)
        b = run_bench(TASKS, configs=["haiku@medium", "opus@medium", "router", "cascade", "cheap-first:opus"], simulate=True)
        self.assertEqual(a, b)
        self.assertTrue(a["simulated"])
        text = format_table(a)
        self.assertTrue(text.startswith("SIMULATED RUN"))
        rows = {r["config"]: r for r in a["rows"]}
        self.assertEqual(rows["haiku@medium"]["avg_attempts"], 1.0)
        self.assertGreater(rows["cascade"]["avg_attempts"], 1.0)
        self.assertGreaterEqual(rows["cascade"]["pass_rate"], rows["router"]["pass_rate"])

    def test_real_path_with_injected_call(self):
        with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False) as f:
            f.write(json.dumps({"id": "a", "prompt": "Extract the id from 'Order #A-7'.", "check": {"type": "contains", "value": "A-7"}}) + "\n")
            f.write(json.dumps({"id": "b", "prompt": "Extract the id from 'Order #B-9'.", "check": {"type": "contains", "value": "B-9"}}) + "\n")
            path = f.name

        def call(model, effort, prompt, feedback=None):
            return CallResult("A-7", {"input_tokens": 100, "output_tokens": 10})
        res = run_bench(path, configs=["haiku@low"], call=call)
        row = res["rows"][0]
        self.assertFalse(res["simulated"])
        self.assertEqual((row["passed"], row["tasks"]), (1, 2))
        self.assertAlmostEqual(row["cost_per_finished_task"], 2 * (100 * 0.10 + 10 * 0.50) / 1e6)
        os.unlink(path)

    def test_bad_config_is_a_clear_error(self):
        with self.assertRaises(RuntimeError):
            run_bench(TASKS, configs=["gpt@high"], simulate=True)


class Cli(unittest.TestCase):
    def test_route_text_json_quiet(self):
        p = cli("route", "Find every caller of parse_config() and list the file paths")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("haiku @ medium", p.stdout)
        d = json.loads(cli("route", "--json", "Plan the database migration from MySQL to Postgres").stdout)
        self.assertEqual((d["tier"], d["effort"], d["task_class"]), ("opus", "high", "design"))
        self.assertTrue(d["ladder"] and d["evidence"] and d["reasons"])
        self.assertEqual(cli("route", "-q", "Fix the typo in README.md").stdout.strip(), "claude-sonnet-5-5 medium")
        self.assertEqual(cli("route", "-q", "--platform", "claude-code", "Fix the typo in README.md").stdout.strip(),
                         "sonnet medium")

    def test_route_from_stdin(self):
        p = cli("route", "-", stdin="Classify these 5,000 tickets by product area as JSON")
        self.assertIn("haiku @ low", p.stdout)

    def test_route_without_task_fails_cleanly(self):
        p = cli("route", stdin="")
        self.assertNotEqual(p.returncode, 0)
        self.assertIn("give the task", p.stderr)

    def test_other_commands(self):
        self.assertIn("sonnet@high  ->  opus@medium", cli("ladder", "sonnet", "medium").stdout)
        self.assertIn("start the session on opus", cli("session", "Design the architecture for a ledger").stdout)
        self.assertIn("$6.000000", cli("cost", "--model", "opus", "--input", "1000000", "--output", "100000").stdout)
        self.assertEqual(cli("cost", "--model", "gpt-9").returncode, 1)
        self.assertIn("No subagent calls recorded yet", cli("report").stdout)
        self.assertIn("ladder", cli("policy").stdout)
        self.assertEqual(cli("doctor").returncode, 0)
        self.assertIn("SIMULATED RUN", cli("bench", TASKS, "--simulate").stdout)
        self.assertIn("tier accuracy", cli("eval", os.path.join(ROOT, "tests", "cases_dev.jsonl")).stdout)
        self.assertIn("gearshift 1.4.1", cli("--version").stdout)


if __name__ == "__main__":
    unittest.main()
