#!/usr/bin/env python3
"""secret-shield UserPromptSubmit hook: block prompts that contain credentials.

Escape hatch: include the text `secret-shield:allow` in the prompt to send it
anyway (the secret is then logged in the register as exposed-in-context).
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import common  # noqa: E402
import detectors  # noqa: E402
import register  # noqa: E402


def handle(data):
    prompt = data.get("prompt") or ""
    findings = common.active(detectors.find_secrets(prompt))
    if not findings:
        return None
    if "secret-shield:allow" in prompt:
        register.record(findings, "prompt (sent with secret-shield:allow)", "exposed-in-context")
        return {"systemMessage": "secret-shield: sent with %s at your request; it is now in "
                                 "Claude's context and logged for rotation." % common.summary(findings)}
    stop = common.blocking(findings)
    if not stop:
        register.record(findings, "prompt", "warned")
        return {"systemMessage": "secret-shield: your prompt contains %s. Test-mode keys are low "
                                 "risk, but an environment variable is still better." % common.summary(findings)}
    register.record(findings, "prompt", "blocked")
    reason = ("secret-shield blocked this prompt: it contains %s. Nothing was sent. Put the value in "
              "an environment variable (e.g. export %s=... in your shell, or a gitignored .env file) "
              "and refer to it by name instead of pasting it. To send it anyway, add "
              "'secret-shield:allow' to the prompt. %s"
              % (common.summary(findings), common.env_advice(stop), common.ignore_hint(stop)))
    return {"decision": "block", "reason": reason}


if __name__ == "__main__":
    common.run_safely(handle)
