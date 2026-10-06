#!/usr/bin/env python3
"""Run prompt test suites and save results.

  run.py [suite ...] [--project DIR] [--provider auto|anthropic|replay] [--record]
         [--model ID] [--case ID] [--quiet]

Suites are prompt-tests/*.json in the project. Results go to prompt-tests/.results/<timestamp>.json.
--record calls the live model and saves each output as the golden copy for replay.
Exit codes: 0 all passed, 1 some failed or errored, 2 configuration problem.
"""
import argparse
import datetime as dt
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import assertions  # noqa: E402
import pr_common as pc  # noqa: E402
from providers import AnthropicProvider, ProviderError, make_provider  # noqa: E402


def pick_provider(requested, suite, record):
    name = requested
    if name == "auto":
        name = suite.get("provider") or ("anthropic" if (record or os.environ.get("ANTHROPIC_API_KEY")) else "replay")
    return name


def run_case(suite, case, provider, args, judge):
    model = args.model or case.get("model") or suite["model"]
    req = {"suite": suite["name"], "case_id": case["id"], "model": model, "system": suite["system"],
           "messages": pc.to_messages(case["input"]),
           "max_tokens": case.get("max_tokens", suite["max_tokens"]),
           "temperature": case.get("temperature", suite["temperature"])}
    result = {"suite": suite["name"], "case": case["id"], "model": model, "provider": provider.name}
    try:
        res = provider.complete(req)
    except ProviderError as exc:
        result.update(status="error", passed=False, error=str(exc), output=None, assertions=[])
        return result, None
    gp = pc.golden_path(args.project, suite["name"], case["id"])
    golden = None
    if args.record:
        golden = res["output"]
    elif gp.is_file():
        try:
            golden = json.loads(gp.read_text(encoding="utf-8")).get("output")
        except ValueError:
            golden = None
    ctx = {"golden": golden, "case": case, "judge": judge}
    checks = [assertions.evaluate(a, res["output"], ctx) for a in case.get("expect", [])]
    failed = any(c["passed"] is False for c in checks)
    result.update(status="fail" if failed else "pass", passed=not failed, output=res["output"],
                  latency_ms=res.get("latency_ms"), usage=res.get("usage") or {},
                  replayed=bool(res.get("replayed")), assertions=checks)
    return result, res


def record_golden(project, suite, case, res, model):
    p = pc.golden_path(project, suite["name"], case["id"])
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"suite": suite["name"], "case": case["id"], "model": res.get("model") or model,
                             "input": case["input"], "output": res["output"], "usage": res.get("usage"),
                             "latency_ms": res.get("latency_ms"),
                             "recorded_at": dt.datetime.now().astimezone().isoformat(timespec="seconds")},
                            indent=2) + "\n", encoding="utf-8")


def results_file(project):
    d = pc.results_dir(project)
    d.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now().strftime("%Y-%m-%dT%H%M%S")
    p, n = d / f"{stamp}.json", 2
    while p.exists():
        p, n = d / f"{stamp}-{n}.json", n + 1
    return p


def main(argv=None, out=sys.stdout):
    ap = argparse.ArgumentParser(description="Run prompt regression suites.")
    ap.add_argument("suites", nargs="*", help="Suite names or files (default: all in prompt-tests/)")
    ap.add_argument("--project", default=os.getcwd())
    ap.add_argument("--provider", default="auto", choices=["auto", "anthropic", "replay"])
    ap.add_argument("--record", action="store_true", help="Call the live model and save outputs as golden copies")
    ap.add_argument("--model", help="Override the model for every case")
    ap.add_argument("--case", action="append", help="Only run this case id (repeatable)")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args(argv)
    args.project = os.path.abspath(args.project)

    files = pc.suite_files(args.project)
    if not files:
        print(f"No suites found in {pc.tests_dir(args.project)}. Run /prompt-regression:init to create one.", file=out)
        return 2
    suites = []
    try:
        for f in files:
            s = pc.load_suite(f, args.project)
            if not args.suites or s["name"] in args.suites or f.stem in args.suites or f.name in args.suites:
                suites.append(s)
    except pc.SuiteError as exc:
        print(f"Suite problem: {exc}", file=out)
        return 2
    if not suites:
        print(f"No suite matches {args.suites}. Available: {', '.join(f.stem for f in files)}", file=out)
        return 2
    if args.record and args.provider == "replay":
        print("--record needs a live model; use --provider anthropic (or auto with ANTHROPIC_API_KEY set).", file=out)
        return 2

    judge = None
    if os.environ.get("ANTHROPIC_API_KEY"):
        live = AnthropicProvider()
        judge = lambda rubric, output, case, a: live.judge(rubric, output, case, a, a.get("model") or pc.DEFAULT_MODEL)

    started = dt.datetime.now().astimezone()
    cases_out = []
    for suite in suites:
        pname = pick_provider(args.provider, suite, args.record)
        try:
            provider = make_provider(pname, args.project)
        except ProviderError as exc:
            print(str(exc), file=out)
            return 2
        if not args.quiet:
            print(f"Suite \"{suite['name']}\" — {len(suite['cases'])} cases, provider {pname}, model "
                  f"{args.model or suite['model']}{' (recording)' if args.record else ''}", file=out)
        for case in suite["cases"]:
            if args.case and case["id"] not in args.case:
                continue
            try:
                result, res = run_case(suite, case, provider, args, judge)
            except pc.SuiteError as exc:
                result, res = {"suite": suite["name"], "case": case["id"], "status": "error", "passed": False,
                               "error": str(exc), "output": None, "assertions": []}, None
            if args.record and res is not None:
                record_golden(args.project, suite, case, res, result.get("model"))
            cases_out.append(result)
            if not args.quiet:
                lat = f"{result['latency_ms']} ms" if result.get("latency_ms") is not None else ""
                print(f"  {result['status'].upper():<5} {case['id']:<28} {lat}", file=out)
                if result["status"] == "error":
                    print(f"        {result['error']}", file=out)
                for c in result["assertions"]:
                    if c["passed"] is False:
                        print(f"        ✗ {c['type']}: {c['detail']}", file=out)
                    elif c["passed"] is None:
                        print(f"        – {c['type']}: {c['detail']}", file=out)

    summary = {"total": len(cases_out), "passed": sum(c["status"] == "pass" for c in cases_out),
               "failed": sum(c["status"] == "fail" for c in cases_out),
               "errors": sum(c["status"] == "error" for c in cases_out),
               "skipped_assertions": sum(a["passed"] is None for c in cases_out for a in c["assertions"])}
    path = results_file(args.project)
    path.write_text(json.dumps({"started_at": started.isoformat(timespec="seconds"),
                                "finished_at": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
                                "project": args.project, "record": args.record,
                                "suites": [s["name"] for s in suites], "summary": summary, "cases": cases_out},
                               indent=2) + "\n", encoding="utf-8")
    rel = os.path.relpath(path, args.project)
    line = f"{summary['passed']}/{summary['total']} passed"
    if summary["failed"]:
        line += f", {summary['failed']} failed"
    if summary["errors"]:
        line += f", {summary['errors']} errors"
    if summary["skipped_assertions"]:
        line += f", {summary['skipped_assertions']} assertions skipped"
    print(f"\n{line}. Results: {rel}", file=out)
    if args.record:
        print(f"Golden outputs saved under {os.path.relpath(pc.golden_dir(args.project), args.project)}/ — "
              "review them before committing.", file=out)
    return 0 if summary["passed"] == summary["total"] else 1


if __name__ == "__main__":
    sys.exit(main())
