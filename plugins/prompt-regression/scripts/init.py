#!/usr/bin/env python3
"""Scaffold prompt-tests/example.json in a project (never overwrites)."""
import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pr_common as pc  # noqa: E402

EXAMPLE = {
    "name": "example",
    "system": "You are a support assistant for Acme Outdoor. Answer in at most three sentences. "
              "Refunds are available within 30 days with a receipt. If the user asks for structured "
              "data, reply with JSON only.",
    "model": pc.DEFAULT_MODEL,
    "max_tokens": 400,
    "cases": [
        {"id": "refund-policy",
         "input": "Can I return a tent I bought three weeks ago?",
         "expect": [
             {"type": "contains", "value": "30 days"},
             {"type": "not_contains", "value": ["I'm not sure", "as an AI"], "ignore_case": True},
             {"type": "max_chars", "value": 500},
             {"type": "llm_judge", "rubric": "Says the return is allowed and mentions needing a receipt."}]},
        {"id": "order-json",
         "input": [{"role": "user", "content": "Give me my order status as JSON with fields order_id and status. Order 1182 shipped."}],
         "expect": [
             {"type": "json_valid"},
             {"type": "json_schema", "schema": {
                 "type": "object", "required": ["order_id", "status"],
                 "properties": {"order_id": {"type": ["string", "integer"]},
                                "status": {"type": "string", "enum": ["pending", "shipped", "delivered"]}}}}]},
        {"id": "greeting-stays-on-brand",
         "input": "hi",
         "expect": [
             {"type": "regex", "pattern": "acme|help", "flags": "i"},
             {"type": "similar_to_golden", "threshold": 0.5}]},
    ],
}


def main(argv=None, out=sys.stdout):
    ap = argparse.ArgumentParser(description="Create prompt-tests/example.json")
    ap.add_argument("--project", default=os.getcwd())
    a = ap.parse_args(argv)
    target = pc.tests_dir(a.project) / "example.json"
    if target.exists():
        print(f"{target} already exists; leaving it alone.", file=out)
        return 1
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(EXAMPLE, indent=2) + "\n", encoding="utf-8")
    print(f"Created {target}", file=out)
    print("Next: replace the example cases with real inputs from your app, then run "
          "/prompt-regression:record (live model) and /prompt-regression:run.", file=out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
