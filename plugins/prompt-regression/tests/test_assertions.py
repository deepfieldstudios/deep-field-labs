"""Every assertion type, shorthand forms, the schema subset, and llm_judge skipping."""
import os
import unittest
from unittest import mock

import helpers  # noqa: F401  (sets sys.path)
import assertions as A


def ev(a, out, **ctx):
    return A.evaluate(a, out, ctx)


class TestAssertions(unittest.TestCase):
    def test_contains(self):
        self.assertTrue(ev({"type": "contains", "value": "30 days"}, "within 30 days")["passed"])
        self.assertFalse(ev({"type": "contains", "value": ["30 days", "receipt"]}, "within 30 days")["passed"])
        self.assertTrue(ev({"type": "contains", "value": "RECEIPT", "ignore_case": True}, "a receipt")["passed"])
        self.assertTrue(ev({"contains": "x"}, "xyz")["passed"])  # shorthand

    def test_not_contains(self):
        self.assertTrue(ev({"type": "not_contains", "value": "sorry"}, "Sure!")["passed"])
        r = ev({"type": "not_contains", "value": ["As an AI"], "ignore_case": True}, "as an ai, I")
        self.assertFalse(r["passed"])
        self.assertIn("As an AI", r["detail"])

    def test_regex(self):
        self.assertTrue(ev({"type": "regex", "pattern": r"order \d{4}"}, "Your order 1182")["passed"])
        self.assertTrue(ev({"type": "regex", "pattern": "^hello", "flags": "im"}, "x\nHello")["passed"])
        self.assertFalse(ev({"regex": r"\d"}, "none")["passed"])
        self.assertFalse(ev({"type": "regex", "pattern": "("}, "x")["passed"])  # bad pattern fails cleanly

    def test_json_valid(self):
        self.assertTrue(ev({"type": "json_valid"}, '{"a": 1}')["passed"])
        self.assertTrue(ev({"type": "json_valid"}, '```json\n{"a": 1}\n```')["passed"])
        self.assertFalse(ev({"type": "json_valid", "allow_fences": False}, '```json\n{"a": 1}\n```')["passed"])
        self.assertFalse(ev({"json_valid": True}, "{a: 1}")["passed"])

    def test_json_schema(self):
        schema = {"type": "object", "required": ["id", "status", "items"],
                  "properties": {"id": {"type": ["string", "integer"]},
                                 "status": {"type": "string", "enum": ["open", "closed"]},
                                 "items": {"type": "array", "items": {"type": "object", "required": ["sku"],
                                                                      "properties": {"qty": {"type": "integer"}}}}}}
        good = '{"id": 7, "status": "open", "items": [{"sku": "A", "qty": 2}]}'
        self.assertTrue(ev({"type": "json_schema", "schema": schema}, good)["passed"])
        bad = '{"id": true, "status": "lost", "items": [{"qty": 1.5}]}'
        r = ev({"type": "json_schema", "schema": schema}, bad)
        self.assertFalse(r["passed"])
        for frag in ["$.id: expected string or integer", "not one of", "$.items[0]: missing required property 'sku'",
                     "$.items[0].qty: expected integer"]:
            self.assertIn(frag, r["detail"])
        self.assertFalse(ev({"json_schema": {"type": "array"}}, '{"a": 1}')["passed"])
        self.assertFalse(ev({"json_schema": {"type": "object"}}, "nope")["passed"])
        self.assertTrue(ev({"json_schema": {"type": "number"}}, "3.5")["passed"])
        self.assertFalse(ev({"json_schema": {"type": "number"}}, "true")["passed"])  # bool isn't a number

    def test_max_chars_and_equals(self):
        self.assertTrue(ev({"type": "max_chars", "value": 5}, "12345")["passed"])
        self.assertFalse(ev({"max_chars": 4}, "12345")["passed"])
        self.assertTrue(ev({"type": "equals", "value": "billing"}, " billing\n")["passed"])
        self.assertFalse(ev({"type": "equals", "value": "billing", "strip": False}, " billing")["passed"])
        self.assertTrue(ev({"equals": "Billing", "ignore_case": True}, "billing")["passed"])

    def test_similar_to_golden(self):
        r = ev({"type": "similar_to_golden", "threshold": 0.8}, "The refund window is 30 days.",
               golden="The refund window is 30 days!")
        self.assertTrue(r["passed"])
        self.assertIn("similarity", r["detail"])
        self.assertFalse(ev({"similar_to_golden": 0.9}, "Totally different text", golden="The refund window")["passed"])
        self.assertFalse(ev({"type": "similar_to_golden"}, "x")["passed"])  # no golden recorded
        self.assertTrue(ev({"type": "similar_to_golden", "value": "abc"}, "abc")["passed"])  # inline golden

    def test_llm_judge_skipped_without_key(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            r = ev({"type": "llm_judge", "rubric": "polite"}, "hi", judge=lambda *a: {"pass": True})
        self.assertIsNone(r["passed"])
        self.assertIn("skipped", r["detail"])

    def test_llm_judge_with_key(self):
        calls = []

        def judge(rubric, output, case, a):
            calls.append(rubric)
            return {"pass": True, "score": 0.9, "reason": "polite"}
        with mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": "k"}):
            self.assertTrue(ev({"llm_judge": "Is polite"}, "hi", judge=judge)["passed"])
            self.assertFalse(ev({"type": "llm_judge", "rubric": "x", "threshold": 0.95}, "hi", judge=judge)["passed"])
            with mock.patch.dict(os.environ, {"PROMPT_REGRESSION_JUDGE": "off"}):
                self.assertIsNone(ev({"llm_judge": "x"}, "hi", judge=judge)["passed"])
            boom = ev({"llm_judge": "x"}, "hi", judge=lambda *a: 1 / 0)
            self.assertFalse(boom["passed"])
        self.assertEqual(calls, ["Is polite", "x"])

    def test_invalid(self):
        self.assertFalse(ev({"type": "nonsense"}, "x")["passed"])
        self.assertFalse(ev({"foo": 1}, "x")["passed"])


if __name__ == "__main__":
    unittest.main()
