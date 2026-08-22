#!/usr/bin/env python3
"""Run manifest-declared Codex Bash gates in one interpreter process."""

from __future__ import annotations

import argparse
import io
import json
import os
import runpy
import signal
import sys
from pathlib import Path
from types import ModuleType
from typing import Any


DECISION_STRENGTH = {"allow": 1, "ask": 2, "deny": 3}
MAX_PAYLOAD_BYTES = 1_048_576


class GateTimeoutError(BaseException):
    """Raised when one gate exceeds its manifest-declared budget."""


def _unique(values: list[str]) -> list[str]:
    return list(dict.fromkeys(value for value in values if value))


def _gate_path(plugin_root: Path, relative: str) -> Path:
    candidate = Path(relative)
    if candidate.is_absolute() or ".." in candidate.parts:
        raise ValueError(f"gate is outside plugin root: {relative}")
    resolved = (plugin_root / candidate).resolve()
    try:
        resolved.relative_to(plugin_root.resolve())
    except ValueError as exc:
        raise ValueError(f"gate is outside plugin root: {relative}") from exc
    if not resolved.is_file():
        raise ValueError(f"gate does not exist inside plugin root: {relative}")
    return resolved


def _run_gate(
    path: Path,
    payload: str,
    timeout_seconds: float | None,
) -> tuple[dict[str, Any] | None, str | None]:
    stdout = io.StringIO()
    stderr = io.StringIO()
    prior_streams = (sys.stdin, sys.stdout, sys.stderr)
    prior_argv = sys.argv
    prior_cwd = Path.cwd()
    prior_environment = dict(os.environ)
    prior_path = list(sys.path)
    prior_modules = dict(sys.modules)
    prior_module_namespaces = []
    seen_modules: set[int] = set()
    for module in prior_modules.values():
        if not isinstance(module, ModuleType) or id(module) in seen_modules:
            continue
        seen_modules.add(id(module))
        prior_module_namespaces.append((module, dict(vars(module))))
    prior_alarm_handler = signal.getsignal(signal.SIGALRM)
    prior_timer = signal.getitimer(signal.ITIMER_REAL)
    set_timer = signal.setitimer
    set_signal = signal.signal
    change_directory = os.chdir
    environment = os.environ
    module_registry = sys.modules
    exit_status: object = 0
    failure: str | None = None

    def timeout_handler(_signum: int, _frame: object) -> None:
        raise GateTimeoutError

    try:
        sys.stdin = io.StringIO(payload)
        sys.stdout = stdout
        sys.stderr = stderr
        sys.argv = [str(path)]
        if timeout_seconds is not None:
            set_signal(signal.SIGALRM, timeout_handler)
            set_timer(signal.ITIMER_REAL, timeout_seconds)
        try:
            runpy.run_path(str(path), run_name="__main__")
        except SystemExit as exc:
            exit_status = exc.code
        except GateTimeoutError:
            failure = f"timed out after {timeout_seconds:g}s"
        except Exception as exc:  # A single gate remains host-compatible fail-open.
            failure = f"{type(exc).__name__}: {exc}"
    finally:
        set_timer(signal.ITIMER_REAL, 0)
        set_signal(signal.SIGALRM, prior_alarm_handler)
        set_timer(signal.ITIMER_REAL, *prior_timer)
        change_directory(prior_cwd)
        environment.clear()
        environment.update(prior_environment)
        for module, namespace in prior_module_namespaces:
            current = vars(module)
            for name in tuple(current):
                if name not in namespace:
                    del current[name]
            current.update(namespace)
        module_registry.clear()
        module_registry.update(prior_modules)
        sys.stdin, sys.stdout, sys.stderr = prior_streams
        sys.argv = prior_argv
        sys.path[:] = prior_path

    if failure is None and exit_status not in (None, 0):
        failure = f"exited with status {exit_status}"
    if failure is not None:
        return None, f"Escapement gate {path.name} failed: {failure}"

    rendered = stdout.getvalue().strip()
    if not rendered:
        return None, None
    try:
        result = json.loads(rendered)
    except json.JSONDecodeError as exc:
        return None, f"Escapement gate {path.name} emitted invalid JSON: {exc}"
    if not isinstance(result, dict):
        return None, f"Escapement gate {path.name} emitted a non-object result"
    return result, None


def _aggregate(
    results: list[dict[str, Any]], warnings: list[str]
) -> dict[str, Any]:
    decisions: list[tuple[str, str]] = []
    contexts: list[str] = []
    messages: list[str] = []
    for result in results:
        message = result.get("systemMessage")
        if isinstance(message, str):
            messages.append(message)
        hook = result.get("hookSpecificOutput")
        if not isinstance(hook, dict):
            continue
        context = hook.get("additionalContext")
        if isinstance(context, str):
            contexts.append(context)
        decision = hook.get("permissionDecision")
        reason = hook.get("permissionDecisionReason")
        if decision in DECISION_STRENGTH:
            decisions.append((decision, reason if isinstance(reason, str) else ""))

    output: dict[str, Any] = {}
    hook_output: dict[str, Any] = {"hookEventName": "PreToolUse"}
    if decisions:
        strongest = max(decisions, key=lambda item: DECISION_STRENGTH[item[0]])[0]
        hook_output["permissionDecision"] = strongest
        reasons = _unique(
            [f"[{decision}] {reason}" for decision, reason in decisions if reason]
        )
        if reasons:
            hook_output["permissionDecisionReason"] = "\n\n".join(reasons)
    contexts = _unique(contexts)
    if contexts:
        hook_output["additionalContext"] = "\n\n".join(contexts)
    if len(hook_output) > 1:
        output["hookSpecificOutput"] = hook_output
    messages = _unique([*messages, *warnings])
    if messages:
        output["systemMessage"] = "\n\n".join(messages)
    return output


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--gate", action="append", required=True)
    parser.add_argument("--gate-timeout", action="append", type=float, default=[])
    args = parser.parse_args(argv)
    if args.gate_timeout and len(args.gate_timeout) != len(args.gate):
        parser.error("each --gate must have one --gate-timeout")
    if any(timeout <= 0 for timeout in args.gate_timeout):
        parser.error("gate timeouts must be positive")
    plugin_root = Path(__file__).resolve().parents[2]
    try:
        gates = [_gate_path(plugin_root, relative) for relative in args.gate]
    except ValueError as exc:
        parser.error(str(exc))

    raw_payload = sys.stdin.buffer.read(MAX_PAYLOAD_BYTES + 1)
    if len(raw_payload) > MAX_PAYLOAD_BYTES:
        print(
            f"FATAL: hook payload exceeds {MAX_PAYLOAD_BYTES} bytes",
            file=sys.stderr,
        )
        return 2
    try:
        payload = raw_payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        print(f"FATAL: hook payload is not UTF-8: {exc}", file=sys.stderr)
        return 2
    timeouts: list[float | None] = args.gate_timeout or [None] * len(gates)
    results: list[dict[str, Any]] = []
    warnings: list[str] = []
    for gate, timeout in zip(gates, timeouts, strict=True):
        result, warning = _run_gate(gate, payload, timeout)
        if result is not None:
            results.append(result)
        if warning is not None:
            warnings.append(warning)
    print(json.dumps(_aggregate(results, warnings)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
