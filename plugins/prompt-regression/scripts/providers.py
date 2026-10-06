"""Provider adapters. Each has complete(request) -> {"output", "latency_ms", "usage", "model", ...}.

request = {"suite", "case_id", "model", "system", "messages", "max_tokens", "temperature"}

- AnthropicProvider: POST https://api.anthropic.com/v1/messages with urllib (stdlib only).
- ReplayProvider: returns outputs recorded in prompt-tests/.golden/<suite>/<case>.json, so suites
  run offline and in CI without an API key.
"""
import json
import os
import time
import urllib.error
import urllib.request

from pr_common import golden_path

API_URL = "https://api.anthropic.com/v1/messages"
API_VERSION = "2023-06-01"


class ProviderError(Exception):
    pass


class AnthropicProvider:
    name = "anthropic"

    def __init__(self, api_key=None, url=None, timeout=120, retries=2, sleep=time.sleep):
        self.api_key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        base = os.environ.get("ANTHROPIC_BASE_URL")
        self.url = url or (base.rstrip("/") + "/v1/messages" if base else API_URL)
        self.timeout = timeout
        self.retries = retries
        self.sleep = sleep

    def build_request(self, req):
        if not self.api_key:
            raise ProviderError("ANTHROPIC_API_KEY is not set")
        body = {"model": req["model"], "max_tokens": int(req.get("max_tokens") or 1024),
                "messages": req["messages"]}
        if req.get("system"):
            body["system"] = req["system"]
        if req.get("temperature") is not None:
            body["temperature"] = req["temperature"]
        return urllib.request.Request(
            self.url, data=json.dumps(body).encode("utf-8"), method="POST",
            headers={"x-api-key": self.api_key, "anthropic-version": API_VERSION,
                     "content-type": "application/json"})

    def complete(self, req):
        http_req = self.build_request(req)
        attempt = 0
        while True:
            start = time.monotonic()
            try:
                with urllib.request.urlopen(http_req, timeout=self.timeout) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                break
            except urllib.error.HTTPError as exc:
                detail = exc.read().decode("utf-8", errors="replace")[:500] if exc.fp else ""
                exc.close()
                if exc.code in (429, 500, 502, 503, 529) and attempt < self.retries:
                    attempt += 1
                    self.sleep(2 ** attempt)
                    continue
                raise ProviderError(f"HTTP {exc.code}: {detail}")
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                if attempt < self.retries:
                    attempt += 1
                    self.sleep(2 ** attempt)
                    continue
                raise ProviderError(f"network error: {exc}")
        latency = int((time.monotonic() - start) * 1000)
        text = "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")
        usage = data.get("usage") or {}
        return {"output": text, "latency_ms": latency, "model": data.get("model", req["model"]),
                "stop_reason": data.get("stop_reason"),
                "usage": {"input_tokens": usage.get("input_tokens"), "output_tokens": usage.get("output_tokens")}}

    def judge(self, rubric, output, case, assertion, model):
        """Score output against a rubric. Returns {"pass", "score", "reason"}."""
        prompt = ("You are grading the output of an AI feature against a rubric.\n\n"
                  f"<rubric>\n{rubric}\n</rubric>\n\n<input>\n{json.dumps(case.get('input'))}\n</input>\n\n"
                  f"<output>\n{output}\n</output>\n\n"
                  'Reply with only a JSON object: {"pass": true|false, "score": <0.0-1.0>, "reason": "<one sentence>"}')
        res = self.complete({"model": assertion.get("model") or model, "max_tokens": 300, "temperature": 0,
                             "messages": [{"role": "user", "content": prompt}]})
        text = res["output"].strip()
        start, end = text.find("{"), text.rfind("}")
        if start < 0 or end < 0:
            raise ProviderError(f"judge did not return JSON: {text[:200]}")
        return json.loads(text[start:end + 1])


class ReplayProvider:
    name = "replay"

    def __init__(self, project):
        self.project = project

    def complete(self, req):
        p = golden_path(self.project, req["suite"], req["case_id"])
        if not p.is_file():
            raise ProviderError(f"no recorded output at {p} (run with --record against a live model first)")
        data = json.loads(p.read_text(encoding="utf-8"))
        return {"output": data.get("output", ""), "latency_ms": data.get("latency_ms"),
                "model": data.get("model"), "usage": data.get("usage") or {}, "replayed": True}


def make_provider(name, project):
    if name == "anthropic":
        return AnthropicProvider()
    if name == "replay":
        return ReplayProvider(project)
    raise ProviderError(f"unknown provider '{name}' (use anthropic or replay)")
