import unittest

from tests.helpers import ROOT  # noqa: F401  (sets sys.path)

from gearshift.pricing import cost_usd, resolve_model, tier_of, usage_to_dict


class Pricing(unittest.TestCase):
    def test_plain_request(self):
        # Opus 5.5: $4 in, $20 out per MTok
        c = cost_usd({"input_tokens": 1_000_000, "output_tokens": 100_000}, "claude-opus-5-5")
        self.assertAlmostEqual(c, 4.0 + 2.0)

    def test_all_five_token_kinds(self):
        usage = {"input_tokens": 1000, "output_tokens": 2000, "cache_read_input_tokens": 500_000,
                 "cache_creation": {"ephemeral_5m_input_tokens": 10_000, "ephemeral_1h_input_tokens": 20_000}}
        # opus: in 4, out 20, read 0.20, 5m write 5, 1h write 8
        want = (1000 * 4 + 2000 * 20 + 500_000 * 0.20 + 10_000 * 5 + 20_000 * 8) / 1e6
        self.assertAlmostEqual(cost_usd(usage, "opus"), want)

    def test_cache_writes_without_ttl_breakdown_use_5m(self):
        u = usage_to_dict({"input_tokens": 0, "output_tokens": 0, "cache_creation_input_tokens": 1000})
        self.assertEqual((u["cache_write_5m_tokens"], u["cache_write_1h_tokens"]), (1000, 0))

    def test_haiku_long_prompt_rate_is_per_request(self):
        short = cost_usd({"input_tokens": 100_000, "output_tokens": 1000}, "claude-haiku-5-5")
        self.assertAlmostEqual(short, (100_000 * 0.10 + 1000 * 0.50) / 1e6)
        long = cost_usd({"input_tokens": 100_001, "output_tokens": 1000}, "claude-haiku-5-5")
        self.assertAlmostEqual(long, (100_001 * 0.50 + 1000 * 2.50) / 1e6)
        # cache reads count toward prompt length
        cached = cost_usd({"input_tokens": 10, "output_tokens": 0, "cache_read_input_tokens": 200_000}, "haiku")
        self.assertAlmostEqual(cached, (10 * 0.50 + 200_000 * 0.05) / 1e6)

    def test_fable_52_price(self):
        self.assertAlmostEqual(cost_usd({"input_tokens": 1_000_000, "output_tokens": 1_000_000}, "fable"), 48.0)

    def test_batch_is_half(self):
        u = {"input_tokens": 1_000_000, "output_tokens": 0}
        self.assertAlmostEqual(cost_usd(u, "sonnet", batch=True), 1.0)

    def test_model_spellings(self):
        self.assertEqual(resolve_model("opus"), "claude-opus-5-5")
        self.assertEqual(resolve_model("claude-opus-5-5[1m]"), "claude-opus-5-5")
        self.assertEqual(resolve_model("claude-haiku-4-5-20251001"), "claude-haiku-4-5")
        self.assertEqual(resolve_model("us.anthropic.claude-opus-4-6-v1:0"), "claude-opus-4-6")
        self.assertEqual(resolve_model("claude-opus-5"), "claude-opus-5")
        self.assertIsNone(resolve_model("gpt-9"))
        self.assertIsNone(resolve_model(""))

    def test_longest_key_wins(self):
        # "claude-opus-5-5" must not be priced as "claude-opus-5"
        self.assertAlmostEqual(cost_usd({"input_tokens": 1_000_000, "output_tokens": 0}, "claude-opus-5-5"), 4.0)
        self.assertAlmostEqual(cost_usd({"input_tokens": 1_000_000, "output_tokens": 0}, "claude-opus-5"), 5.0)

    def test_unknown_model_is_none_not_zero(self):
        self.assertIsNone(cost_usd({"input_tokens": 5, "output_tokens": 5}, "mystery-model"))

    def test_tier_of(self):
        self.assertEqual(tier_of("claude-fable-5-2"), "fable")
        self.assertEqual(tier_of("claude-mythos-5-2"), "fable")
        self.assertEqual(tier_of("some-future-sonnet-9"), "sonnet")
        self.assertIsNone(tier_of("gpt-9"))

    def test_sdk_style_usage_object(self):
        class CC:
            ephemeral_5m_input_tokens = 100
            ephemeral_1h_input_tokens = 0

        class U:
            input_tokens = 10
            output_tokens = 20
            cache_read_input_tokens = None
            cache_creation_input_tokens = 100
            cache_creation = CC()
        self.assertAlmostEqual(cost_usd(U(), "sonnet"), (10 * 2 + 20 * 10 + 100 * 2.5) / 1e6)


if __name__ == "__main__":
    unittest.main()
