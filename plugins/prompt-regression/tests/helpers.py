"""Test helpers: plugin paths, a fake urlopen response, and a throwaway project."""
import io
import json
import sys
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = PLUGIN_ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()


def api_reply(text, model="claude-sonnet-5-5", inp=12, out=7):
    return FakeResponse(json.dumps({
        "id": "msg_1", "type": "message", "role": "assistant", "model": model,
        "content": [{"type": "text", "text": text}], "stop_reason": "end_turn",
        "usage": {"input_tokens": inp, "output_tokens": out}}).encode())


def write_suite(project, suite, name="support"):
    d = Path(project) / "prompt-tests"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{name}.json").write_text(json.dumps(suite))
    return d / f"{name}.json"


def write_golden(project, suite, case, output, **extra):
    p = Path(project) / "prompt-tests" / ".golden" / suite / f"{case}.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(dict({"output": output, "latency_ms": 100,
                                  "usage": {"input_tokens": 10, "output_tokens": 5}}, **extra)))
    return p
