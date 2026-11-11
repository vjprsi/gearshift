import os
import unittest

from tests.helpers import TESTS, clean_router

from gearshift.evalset import evaluate
from gearshift.policy import EFFORTS, TIERS, load_policy, tier_rank
from gearshift.router import Router


GERMAN = "Finde alle Dateien, die das Zahlungsmodul importieren, und gib mir die Liste."
GERMAN_FIX = "Behebe den Fehler im Zahlungsmodul."


class CaseSets(unittest.TestCase):
    def test_spanish_portuguese_regression_set(self):
        r = evaluate(os.path.join(TESTS, "cases_es.jsonl"), clean_router())
        self.assertEqual(r["tier_accuracy"], 1.0, r["misses"])

    def test_spanish_portuguese_blind_set_floor(self):
        # run once blind at 90.0%, both misses were over-routed. Not tuned against.
        r = evaluate(os.path.join(TESTS, "cases_es_blind.jsonl"), clean_router())
        self.assertGreaterEqual(r["tier_accuracy"], 0.85, r["misses"])
        self.assertEqual(r["under_routed"], 0, r["misses"])

    def test_unknown_languages_never_go_cheap_without_a_judge(self):
        # German, French, Italian, Hindi, Japanese. Offline these are "unsure",
        # which means Opus, and the hook leaves an unsure call alone. Expensive
        # sometimes, but nothing lands below its label by accident. (French
        # shares a few verbs with Spanish, so the odd one is classified.)
        from gearshift.evalset import load_cases
        router = clean_router()
        unsure = 0
        for c in load_cases(os.path.join(TESTS, "cases_multi.jsonl")):
            d = router.route(c["task"])
            if d.task_class == "unsure":
                unsure += 1
                self.assertEqual(d.tier, "opus")
            else:
                self.assertGreaterEqual(tier_rank(d.tier), tier_rank(c["tier"]), c["task"])
        self.assertGreaterEqual(unsure, 20)

    def test_perfect_judge_fixes_unknown_languages(self):
        # what the judge is worth IF it classifies correctly: an upper bound, not a measurement
        from gearshift.evalset import load_cases
        cases = {c["task"]: c["class"] for c in load_cases(os.path.join(TESTS, "cases_multi.jsonl"))}
        oracle = Router(load_policy(use_user_files=False),
                        judge=lambda text: cases.get(text.strip().split("\n")[-1]))
        r = evaluate(os.path.join(TESTS, "cases_multi.jsonl"), oracle)
        self.assertGreaterEqual(r["tier_accuracy"], 0.9, r["misses"])
        self.assertEqual(r["under_routed"], 0, r["misses"])

    def test_dev_set_is_a_regression_suite(self):
        r = evaluate(os.path.join(TESTS, "cases_dev.jsonl"), clean_router())
        self.assertEqual(r["under_routed"], 0, r["misses"])
        self.assertEqual(r["tier_accuracy"], 1.0, r["misses"])
        self.assertEqual(r["effort_accuracy"], 1.0, r["misses"])
        self.assertGreaterEqual(r["class_accuracy"], 0.97)

    def test_formerly_held_out_sets_stay_fixed(self):
        # Sets 1-3 were each run once blind, then mined for fixes (see
        # docs/ACCURACY.md for the blind scores). They are regression suites now.
        for name in ("cases_holdout.jsonl", "cases_holdout2.jsonl", "cases_holdout3.jsonl"):
            r = evaluate(os.path.join(TESTS, name), clean_router())
            self.assertGreaterEqual(r["tier_accuracy"], 0.95, (name, r["misses"]))
            self.assertLessEqual(r["under_routed"], 1, (name, r["misses"]))

    def test_unseen_set_floor(self):
        # Set 4 has never been tuned against. Its blind score was 90.0%.
        r = evaluate(os.path.join(TESTS, "cases_holdout4.jsonl"), clean_router())
        self.assertGreaterEqual(r["tier_accuracy"], 0.85, r["misses"])
        self.assertLessEqual(r["confident_under_routed"], 1, r["misses"])

    def test_haiku_is_never_given_work_labelled_above_it(self):
        from gearshift.evalset import load_cases
        router = clean_router()
        for name in ("cases_dev.jsonl", "cases_holdout.jsonl", "cases_holdout2.jsonl",
                     "cases_holdout3.jsonl", "cases_holdout4.jsonl"):
            for c in load_cases(os.path.join(TESTS, name)):
                d = router.route(c["task"], description=c.get("description"),
                                 agent_type=c.get("agent_type"), platform=c.get("platform", "api"))
                if d.tier == "haiku":
                    self.assertEqual(c["tier"], "haiku", c["task"])


class Decisions(unittest.TestCase):
    def setUp(self):
        self.r = clean_router()

    def test_lookup_goes_to_haiku_with_reasons(self):
        d = self.r.route("Find every caller of parse_config() and list the file paths.")
        self.assertEqual((d.tier, d.effort, d.task_class), ("haiku", "medium", "lookup"))
        self.assertEqual(d.model, "claude-haiku-5-5")
        self.assertTrue(d.reasons and d.classification.evidence)
        self.assertIn("haiku @ medium", d.explain())

    def test_api_params_shape(self):
        d = self.r.route("Classify these 5,000 tickets by product area as JSON.")
        self.assertEqual(d.api_params(), {"model": "claude-haiku-5-5", "output_config": {"effort": "low"}})
        self.assertEqual(d.claude_code_params(), {"model": "haiku", "effort": "low"})
        self.assertIn("cheap-first", d.strategy)

    def test_bug_hunt_is_never_haiku(self):
        for task in ("Find the bug.", "Find bugs in the order pipeline.", "Look for vulnerabilities in auth.py"):
            self.assertGreaterEqual(tier_rank(self.r.route(task).tier), tier_rank("sonnet"), task)

    def test_a_fix_is_never_haiku(self):
        for task in ("Find where the timeout is set and change it to 60.",
                     "Run the tests and fix whatever fails.",
                     "Search for the deprecated call and replace it everywhere."):
            self.assertGreaterEqual(tier_rank(self.r.route(task).tier), tier_rank("sonnet"), task)

    def test_read_only_instruction_beats_edit_verbs(self):
        d = self.r.route("We will later fix and refactor the parser. For now list every file that imports it. "
                         "Do not modify any files.")
        self.assertEqual(d.tier, "haiku")

    def test_file_names_are_not_read_as_instructions(self):
        d = self.r.route("Extract the duplicated validation code in signup.py and profile.py into one helper.")
        self.assertEqual((d.task_class, d.tier), ("edit", "sonnet"))   # "profile.py" is not "profile the code"

    def test_nouns_in_a_list_are_not_verbs(self):
        d = self.r.route("Tag these support emails with the product area (billing, login, shipping, other).")
        self.assertEqual((d.task_class, d.tier, d.effort), ("extract", "haiku", "low"))

    def test_repeating_a_verb_is_not_evidence(self):
        once = self.r.classify("Add audit logging to the admin API.").scores["edit"]
        thrice = self.r.classify("Add audit logging to the admin API. Add it. Add it. Add it.").scores["edit"]
        self.assertLess(thrice, once * 1.6)

    def test_scope_limit_is_not_read_only(self):
        d = self.r.route("Replace the old client call in refunds.py with the new one. Don't touch other files.")
        self.assertEqual(d.task_class, "edit")

    def test_stakes_raise_effort_and_floor(self):
        d = self.r.route("Fix the rounding bug in the production billing service.")
        self.assertEqual((d.tier, d.effort), ("sonnet", "high"))
        self.assertIn("stakes", d.flags)

    def test_stakes_alone_never_reach_xhigh(self):
        d = self.r.route("Design the architecture for our production payment authorization system.")
        self.assertEqual((d.tier, d.effort), ("opus", "high"))

    def test_long_horizon_reaches_xhigh_only_on_opus_or_fable(self):
        d = self.r.route("Plan the database migration from MySQL to Postgres and then run unattended overnight.")
        self.assertEqual((d.tier, d.effort), ("opus", "xhigh"))

    def test_never_picks_max(self):
        tasks = ["Ultrathink, be exhaustive and meticulous: prove the theorem, run overnight for hours.",
                 "Think hard and be very thorough: design the architecture for production payments, unattended."]
        for t in tasks:
            for prefer in ("cost", "balanced", "quality"):
                self.assertNotEqual(self.r.route(t, prefer=prefer).effort, "max")

    def test_one_stray_word_does_not_buy_fable(self):
        for task in ("Build a proof of concept for the dashboard.",
                     "Fix the statistical model import in model.py.",
                     "Analyze the statistical model in model.py for bugs.",
                     "Solve the merge conflict in package.json."):
            self.assertNotEqual(self.r.route(task).tier, "fable", task)

    def test_deep_research_is_opus_not_fable(self):
        d = self.r.route("Do deep research on instant payment regulation and write a cited report.")
        self.assertEqual((d.task_class, d.tier, d.effort), ("research", "opus", "high"))

    def test_hardest_reasoning_is_fable(self):
        d = self.r.route("Prove that the algorithm terminates and derive its complexity.")
        self.assertEqual((d.tier, d.effort), ("fable", "high"))

    def test_unsure_goes_to_opus_default(self):
        d = self.r.route("Do it.")
        self.assertEqual((d.task_class, d.tier, d.effort), ("unsure", "opus", "medium"))
        self.assertLess(d.confidence, 0.5)

    def test_empty_task_does_not_crash(self):
        self.assertEqual(self.r.route("").task_class, "unsure")
        self.assertEqual(self.r.route("   \n").task_class, "unsure")

    def test_spanish_and_portuguese_route_offline(self):
        d = self.r.route("Busca todos los archivos que importan el modulo de pagos y dame la lista.")
        self.assertEqual((d.task_class, d.tier), ("lookup", "haiku"))
        d = self.r.route("Corrige o erro de digitação no README.")
        self.assertEqual(d.tier, "sonnet")

    def test_other_languages_are_unsure_without_a_judge(self):
        for t in (GERMAN, "支払いモジュールのバグを修正してください。",
                  "भुगतान मॉड्यूल को आयात करने वाली सभी फ़ाइलें खोजें और सूची दें।"):
            d = self.r.route(t)
            self.assertEqual((d.task_class, d.tier), ("unsure", "opus"), t)

    def test_judge_is_consulted_only_when_unsure(self):
        calls = []

        def judge(text):
            calls.append(text)
            return "lookup"
        r = Router(load_policy(use_user_files=False), judge=judge)
        d = r.route(GERMAN)
        self.assertEqual((d.task_class, d.tier), ("lookup", "haiku"))
        self.assertTrue(d.classification.judged)
        self.assertEqual(len(calls), 1)
        r.route("Find every caller of parse_config() and list the file paths.")
        self.assertEqual(len(calls), 1, "confident decisions must not call the judge")

    def test_judge_failure_is_swallowed(self):
        def judge(text):
            raise RuntimeError("network down")
        r = Router(load_policy(use_user_files=False), judge=judge)
        self.assertEqual(r.route("Mach das.").task_class, "unsure")

    def test_judge_garbage_is_ignored(self):
        for junk in ("banana", 42, {"class": "banana"}, {"flags": ["stakes"]}, ["lookup"]):
            r = Router(load_policy(use_user_files=False), judge=lambda t, j=junk: j)
            self.assertEqual(r.route("Mach das.").task_class, "unsure", junk)

    def test_judge_flags_move_the_decision(self):
        pol = load_policy(use_user_files=False)
        plain = Router(pol, judge=lambda t: {"class": "edit", "flags": []}).route(GERMAN_FIX)
        risky = Router(pol, judge=lambda t: {"class": "edit", "flags": ["mutating", "stakes"]}).route(GERMAN_FIX)
        self.assertEqual((plain.tier, plain.effort), ("sonnet", "medium"))
        self.assertGreater((tier_rank(risky.tier), EFFORTS.index(risky.effort)),
                           (tier_rank(plain.tier), EFFORTS.index(plain.effort)))

    def test_judge_cannot_send_a_code_change_to_haiku(self):
        pol = load_policy(use_user_files=False)
        for cheap in ("lookup", "extract", "chat"):
            d = Router(pol, judge=lambda t, c=cheap: {"class": c, "flags": ["mutating"]}).route(GERMAN_FIX)
            self.assertEqual(d.task_class, "edit", cheap)
            self.assertNotEqual(d.tier, "haiku", cheap)

    def test_judge_always_mode(self):
        calls = []
        r = Router(load_policy(use_user_files=False), judge=lambda t: calls.append(t) or "review",
                   judge_mode="always")
        d = r.route("Find every caller of parse_config() and list the file paths.")
        self.assertEqual((len(calls), d.task_class), (1, "review"))

    def test_judged_decisions_are_confident_enough_to_act(self):
        pol = load_policy(use_user_files=False)
        d = Router(pol, judge=lambda t: "lookup").route(GERMAN)
        self.assertGreaterEqual(d.confidence, pol.thresholds["act_confidence"])


class Constraints(unittest.TestCase):
    def setUp(self):
        self.r = clean_router()
        self.design = "Design the architecture for a real-time fraud scoring service."

    def test_ceiling_caps_and_raises_effort(self):
        d = self.r.route("Implement a notifications feature: database table, API endpoints, a React inbox, and tests.",
                         ceiling="sonnet")
        self.assertEqual((d.tier, d.effort, d.capped_from), ("sonnet", "high", "opus"))
        self.assertTrue(all(tier_rank(t) <= tier_rank("sonnet") for t, _ in d.ladder))

    def test_floor(self):
        d = self.r.route("Find every caller of parse_config().", floor="sonnet")
        self.assertEqual((d.tier, d.effort), ("sonnet", "medium"))

    def test_available_falls_to_next_tier_down(self):
        d = self.r.route("Prove that the algorithm terminates and derive its complexity.",
                         available=["haiku", "sonnet", "opus"])
        self.assertEqual(d.tier, "opus")
        self.assertEqual(d.capped_from, "fable")
        self.assertTrue(all(t != "fable" for t, _ in d.ladder))

    def test_available_with_only_bigger_tiers(self):
        d = self.r.route("Find every caller of parse_config().", available=["opus"])
        self.assertEqual(d.tier, "opus")

    def test_prefer(self):
        base = self.r.route("Review this diff for correctness.")
        cost = self.r.route("Review this diff for correctness.", prefer="cost")
        self.assertLess(EFFORTS.index(cost.effort), EFFORTS.index(base.effort))
        q = self.r.route("Fix the typo in README.md.", prefer="quality")
        self.assertEqual(q.effort, "high")

    def test_task_class_override(self):
        d = self.r.route("whatever this says", task_class="extract")
        self.assertEqual((d.tier, d.effort, d.confidence), ("haiku", "low", 1.0))
        with self.assertRaises(ValueError):
            self.r.route("x", task_class="nope")

    def test_platform_default_flag(self):
        api = self.r.route("Fix the typo in README.md.", platform="api")
        cc = self.r.route("Fix the typo in README.md.", platform="claude-code")
        self.assertEqual((api.effort, cc.effort), ("medium", "medium"))
        self.assertFalse(api.effort_is_default)   # Sonnet 5.5 defaults to high on the API
        self.assertTrue(cc.effort_is_default)     # and to medium in Claude Code

    def test_haiku_long_prompt_note(self):
        d = self.r.route("Find every caller of parse_config().", context_tokens=250000)
        self.assertTrue(any("over 100000 tokens" in n for n in d.notes))


class Ladder(unittest.TestCase):
    def setUp(self):
        self.r = clean_router()

    def test_effort_before_model_then_lower_effort(self):
        self.assertEqual(self.r.ladder("sonnet", "medium")[:3],
                         [("sonnet", "high"), ("opus", "medium"), ("opus", "high")])

    def test_xhigh_before_fable(self):
        self.assertEqual(self.r.ladder("opus", "high"), [("opus", "xhigh"), ("fable", "high"), ("fable", "xhigh")])

    def test_off_ladder_start_joins_it(self):
        self.assertEqual(self.r.ladder("sonnet", "low")[:2], [("sonnet", "medium"), ("sonnet", "high")])
        self.assertEqual(self.r.ladder("fable", "low")[:2], [("fable", "medium"), ("fable", "high")])

    def test_top_of_ladder_is_empty_and_never_max(self):
        self.assertEqual(self.r.ladder("fable", "xhigh"), [])
        for t in TIERS:
            for e in EFFORTS:
                self.assertNotIn("max", [x[1] for x in self.r.ladder(t, e)])

    def test_ladder_is_monotone(self):
        pol = load_policy(use_user_files=False)
        ranks = [(tier_rank(t), EFFORTS.index(e)) for t, e in pol.ladder]
        self.assertEqual(ranks, sorted(ranks))


class SessionAdvice(unittest.TestCase):
    def test_too_small(self):
        r = clean_router()
        adv = r.advise_session("Plan the migration of our entire monolith from Django to FastAPI.", current_tier="haiku")
        self.assertEqual((adv["tier"], adv["mismatch"]), ("opus", "too_small"))
        adv = r.advise_session("Fix the typo in README.md.", current_tier="opus")
        self.assertIsNone(adv["mismatch"])


class PolicyOverrides(unittest.TestCase):
    def test_override_class_and_signal(self):
        pol = load_policy({"classes": {"lookup": {"tier": "sonnet", "effort": "low"}},
                           "signals": [{"name": "house rule", "patterns": ["kyc"], "adds": {}, "flag": "stakes"}]},
                          use_user_files=False)
        r = Router(pol)
        self.assertEqual(r.route("Find every caller of parse_config().").tier, "sonnet")
        self.assertIn("stakes", r.route("Update the KYC form label in form.tsx.").flags)

    def test_bad_user_regex_is_skipped_not_fatal(self):
        pol = load_policy({"signals": [{"name": "broken", "patterns": ["(unclosed"], "adds": {"edit": 5}}]},
                          use_user_files=False)
        self.assertEqual(Router(pol).route("Find every caller of parse_config().").tier, "haiku")

    def test_invalid_tier_is_rejected(self):
        with self.assertRaises(ValueError):
            load_policy({"classes": {"lookup": {"tier": "gigantic", "effort": "low"}}}, use_user_files=False)


if __name__ == "__main__":
    unittest.main()
