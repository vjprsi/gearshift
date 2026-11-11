"""The pack is only worth something if its own checks are right. Every code
task has a reference solution here that must pass, and every numeric answer
is recomputed."""

import itertools
import math
import os
import subprocess
import sys
import tempfile
import unittest
from fractions import Fraction

from tests.helpers import ROOT

from gearshift.bench import check_output, extract_code, load_tasks, run_bench

REFERENCE = {
"m1": '''
import re
def slugify(s):
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")
''',
"m2": '''
def is_palindrome(s):
    t = [c.lower() for c in s if c.isalnum()]
    return t == t[::-1]
''',
"m3": '''
import re
def parse_duration(s):
    m = re.fullmatch(r"(?:(\\d+)h)?(?:(\\d+)m)?(?:(\\d+)s)?", s)
    if not s or not m:
        raise ValueError(s)
    h, mi, se = (int(x) if x else 0 for x in m.groups())
    return h * 3600 + mi * 60 + se
''',
"m4": '''
def merge_intervals(intervals):
    out = []
    for a, b in sorted(intervals):
        if out and a <= out[-1][1]:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return out
''',
"m5": '''
def roman_to_int(s):
    v = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100, "D": 500, "M": 1000}
    total = 0
    for i, c in enumerate(s):
        total += -v[c] if i + 1 < len(s) and v[c] < v[s[i + 1]] else v[c]
    return total
''',
"m6": '''
def binary_search(xs, target):
    lo, hi = 0, len(xs)
    while lo < hi:
        mid = (lo + hi) // 2
        if xs[mid] == target:
            return mid
        if xs[mid] < target:
            lo = mid + 1
        else:
            hi = mid
    return -1
''',
"m7": '''
def luhn_valid(number):
    digits = number.replace(" ", "")
    if not digits or not digits.isdigit():
        return False
    total = 0
    for i, c in enumerate(reversed(digits)):
        d = int(c)
        if i % 2:
            d = d * 2 - 9 if d > 4 else d * 2
        total += d
    return total % 10 == 0
''',
"m8": '''
def parse_csv_line(line):
    out, cur, i, quoted = [], [], 0, False
    while i < len(line):
        c = line[i]
        if quoted:
            if c == '"' and line[i + 1:i + 2] == '"':
                cur.append('"'); i += 1
            elif c == '"':
                quoted = False
            else:
                cur.append(c)
        elif c == '"' and not cur:
            quoted = True
        elif c == ",":
            out.append("".join(cur)); cur = []
        else:
            cur.append(c)
        i += 1
    out.append("".join(cur))
    return out
''',
"h1": '''
from collections import OrderedDict
class LRUCache:
    def __init__(self, capacity):
        self.cap, self.d = capacity, OrderedDict()
    def get(self, key):
        if key not in self.d:
            return None
        self.d.move_to_end(key)
        return self.d[key]
    def put(self, key, value):
        self.d[key] = value
        self.d.move_to_end(key)
        if len(self.d) > self.cap:
            self.d.popitem(last=False)
''',
"h2": '''
def topo_sort(deps):
    graph = {k: list(v) for k, v in deps.items()}
    for v in list(deps.values()):
        for d in v:
            graph.setdefault(d, [])
    state, order = {}, []
    for root in graph:
        if root in state:
            continue
        stack = [(root, iter(graph[root]))]
        state[root] = 1
        while stack:
            node, it = stack[-1]
            for d in it:
                if state.get(d) == 1:
                    raise ValueError("cycle")
                if d not in state:
                    state[d] = 1
                    stack.append((d, iter(graph[d])))
                    break
            else:
                state[node] = 2
                order.append(node)
                stack.pop()
    return order
''',
"h3": '''
def evaluate(expr):
    s, pos = expr.replace(" ", ""), [0]
    def peek():
        return s[pos[0]] if pos[0] < len(s) else ""
    def atom():
        c = peek()
        if c == "-":
            pos[0] += 1
            return -atom()
        if c == "(":
            pos[0] += 1
            v = add()
            pos[0] += 1
            return v
        j = pos[0]
        while peek() and (peek().isdigit() or peek() == "."):
            pos[0] += 1
        return float(s[j:pos[0]])
    def mul():
        v = atom()
        while peek() in ("*", "/") and peek():
            op = peek(); pos[0] += 1
            v = v * atom() if op == "*" else v / atom()
        return v
    def add():
        v = mul()
        while peek() in ("+", "-") and peek():
            op = peek(); pos[0] += 1
            v = v + mul() if op == "+" else v - mul()
        return v
    return float(add())
''',
"h4": '''
class TokenBucket:
    def __init__(self, rate, capacity, clock):
        self.rate, self.cap, self.clock = rate, capacity, clock
        self.tokens, self.last = float(capacity), clock()
    def allow(self):
        now = self.clock()
        self.tokens = min(self.cap, self.tokens + (now - self.last) * self.rate)
        self.last = now
        if self.tokens >= 1:
            self.tokens -= 1
            return True
        return False
''',
"h5": '''
def split_amount(total_cents, weights):
    w = sum(weights)
    base = [total_cents * x // w for x in weights]
    rem = [total_cents * x % w for x in weights]
    left = total_cents - sum(base)
    for i in sorted(range(len(weights)), key=lambda i: (-rem[i], i))[:left]:
        base[i] += 1
    return base
''',
"h6": '''
def max_meetings(intervals):
    n, end = 0, float("-inf")
    for s, e in sorted(intervals, key=lambda x: x[1]):
        if s >= end:
            n, end = n + 1, e
    return n
''',
"h7": '''
from datetime import timedelta
def next_business_day(d, holidays):
    d += timedelta(days=1)
    while d.weekday() >= 5 or d in holidays:
        d += timedelta(days=1)
    return d
''',
}

# plausible wrong answers: the checks must catch these
WRONG = {
"m1": 'def slugify(s):\n    return s.lower().replace(" ", "-")',
"m4": 'def merge_intervals(x):\n    return sorted(x)',
"m6": 'def binary_search(xs, target):\n    return xs.index(target)',
"m7": 'def luhn_valid(n):\n    return n.replace(" ", "").isdigit()',
"h3": 'def evaluate(expr):\n    return float(eval(expr))',
"h5": 'def split_amount(t, w):\n    return [round(t * x / sum(w)) for x in w]',
"h6": 'def max_meetings(iv):\n    return len(iv)',
}


def _tilings(rows, cols):
    from functools import lru_cache

    @lru_cache(None)
    def go(col, mask):
        if col == cols:
            return 1 if mask == 0 else 0
        total = 0

        def fill(r, cur, nxt):
            nonlocal total
            if r == rows:
                total += go(col + 1, nxt)
            elif cur >> r & 1:
                fill(r + 1, cur, nxt)
            else:
                fill(r + 1, cur | 1 << r, nxt | 1 << r)
                if r + 1 < rows and not cur >> (r + 1) & 1:
                    fill(r + 2, cur | 3 << r, nxt)
        fill(0, mask, 0)
        return total
    return go(0, 0)


def _trailing_zeros(n):
    c = 0
    while n:
        n //= 5
        c += n
    return c


class Pack(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tasks = {t["id"]: t for t in load_tasks("pack")}

    def test_shape(self):
        self.assertEqual(len(self.tasks), 31)
        bands = [t["band"] for t in self.tasks.values()]
        self.assertEqual({b: bands.count(b) for b in set(bands)}, {"easy": 8, "code": 8, "hard": 7, "reasoning": 8})
        for t in self.tasks.values():
            self.assertNotEqual(t["check"]["type"], "command", "the shipped pack must not run shell commands")

    def test_every_code_task_has_a_passing_reference(self):
        code_tasks = [i for i, t in self.tasks.items() if t["check"]["type"] == "python"]
        self.assertEqual(sorted(code_tasks), sorted(REFERENCE))
        for tid in code_tasks:
            answer = "Here you go:\n\n```python\n%s\n```\n" % REFERENCE[tid].strip()
            ok, why = check_output(self.tasks[tid], answer)
            self.assertTrue(ok, "%s: %s" % (tid, why))

    def test_wrong_code_fails(self):
        for tid, code in WRONG.items():
            ok, why = check_output(self.tasks[tid], "```python\n%s\n```" % code)
            self.assertFalse(ok, tid)
            self.assertTrue(why)

    def test_endless_loop_times_out(self):
        task = dict(self.tasks["m6"], check=dict(self.tasks["m6"]["check"], timeout=2))
        ok, why = check_output(task, "```python\ndef binary_search(xs, t):\n    while True:\n        pass\n```")
        self.assertEqual((ok, why), (False, "timed out after 2s"))

    def test_extract_code(self):
        self.assertEqual(extract_code("x\n```python\na = 1\n```\ny\n```\nb = 2\nc = 3\n```").strip(), "b = 2\nc = 3")
        self.assertEqual(extract_code("def f(): pass"), "def f(): pass")

    def test_exact_answers(self):
        want = {
            "r1": "%03d" % pow(7, 222, 1000),
            "r2": str(sum(1 for p in itertools.permutations(range(8)) if all(p[i] != i for i in range(8)))),
            "r3": str(next(n for n in range(1, 1000) if _trailing_zeros(n) == 100)),
            "r4": str(_tilings(3, 10)),
            "r5": str(sum(1 for n in range(1, 1000001) if n % 2 and n % 3 and n % 5 and n % 7)),
            "r6": str(Fraction(sum(1 for d in itertools.product(range(1, 7), repeat=3) if sum(d) == 10), 216)),
            "r7": str(math.comb(12, 6) - math.comb(6, 3) ** 2) if hasattr(math, "comb") else "524",
            "r8": str(sum(1 for n in range(1, 1000) if sum(map(int, str(n))) == 10)),
            "e5": "3", "e7": "6015550123", "e3": "2026-03-07",
        }
        for tid, value in want.items():
            self.assertEqual(self.tasks[tid]["check"]["value"], value, tid)
            self.assertTrue(check_output(self.tasks[tid], " %s \n" % value)[0], tid)
        self.assertFalse(check_output(self.tasks["r3"], "The answer is 405.")[0])
        self.assertFalse(check_output(self.tasks["r3"], "405\n\nWait, let me recheck: 410")[0])
        # working above the answer is fine, the last line is what counts
        self.assertTrue(check_output(self.tasks["r7"], "924 - 400 = 524.\n\n**524**\n")[0])
        self.assertTrue(check_output(self.tasks["r6"], "27/216 = 1/8\n\n1/8")[0])
        self.assertEqual(self.tasks["r1"]["check"]["type"], "final")
        self.assertEqual(self.tasks["e5"]["check"]["type"], "equals", "the easy tasks stay strict")

    def test_easy_checks(self):
        ok = {"e1": "FV-20391", "e2": "negative", "e6": "Arango, Bedoya, Cardenas, Mejia, Zuluaga", "e8": "Portuguese",
              "e4": 'Sure:\n{"name": "Laura Restrepo", "amount": 125000, "currency": "COP"}'}
        for tid, out in ok.items():
            self.assertTrue(check_output(self.tasks[tid], out)[0], tid)
        self.assertFalse(check_output(self.tasks["e4"], '{"name": "Laura", "amount": "125000", "currency": "COP"}')[0])
        self.assertFalse(check_output(self.tasks["e6"], "Zuluaga, Arango")[0])

    def test_pack_runs_in_the_harness_and_writes_markdown(self):
        r = run_bench("pack", configs=["haiku@medium", "router", "cascade"], simulate=True)
        self.assertEqual((r["tasks"], r["source"], len(r["rows"])), (31, "gearshift pack", 3))
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "results.md")
            env = dict(os.environ, PYTHONPATH=os.path.join(ROOT, "src"))
            p = subprocess.run([sys.executable, "-m", "gearshift", "bench", "pack", "--simulate",
                                "--configs", "sonnet@medium,router", "--out", out],
                               capture_output=True, text=True, env=env)
            self.assertEqual(p.returncode, 0, p.stderr)
            with open(out, encoding="utf-8") as f:
                md = f.read()
        self.assertIn("SIMULATED RUN", md)
        self.assertIn("| `router` |", md)
        self.assertIn("claude-sonnet-5-5", md)


if __name__ == "__main__":
    unittest.main()
