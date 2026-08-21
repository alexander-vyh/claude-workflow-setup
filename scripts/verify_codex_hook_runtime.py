#!/usr/bin/env python3
"""Verify the effective Codex hook topology and dispatcher behavior."""

from __future__ import annotations

import argparse
import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from prune_codex_hooks import plugin_owned_gate_scripts, prune_hooks


PLUGIN_ID = "escapement@escapement"


def _fatal(message: str) -> None:
    raise SystemExit(f"FATAL: {message}")


def _installed_plugin_root(codex_home: Path, codex_bin: str) -> Path:
    result = subprocess.run(
        [codex_bin, "plugin", "list", "--marketplace", "escapement", "--json"],
        text=True,
        capture_output=True,
        check=False,
        timeout=15,
        env=os.environ | {"CODEX_HOME": str(codex_home)},
    )
    if result.returncode != 0:
        _fatal(f"could not inspect installed Codex plugins: {result.stderr.strip()}")
    data = json.loads(result.stdout)
    for item in data.get("installed", []):
        if item.get("pluginId") != PLUGIN_ID or not item.get("enabled", True):
            continue
        parts = (item.get("marketplaceName"), item.get("name"), item.get("version"))
        if all(
            isinstance(part, str)
            and part
            and "/" not in part
            and part not in {".", ".."}
            for part in parts
        ):
            cached = codex_home / "plugins" / "cache" / Path(*parts)
            resolved_cache = cached.resolve()
            selected_root = codex_home.resolve()
            try:
                resolved_cache.relative_to(selected_root)
            except ValueError:
                _fatal("installed plugin cache escapes selected Codex home")
            if (cached / "hooks" / "hooks.json").is_file():
                return cached
        _fatal("enabled Escapement plugin has no cache in selected Codex home")
    _fatal("enabled Escapement plugin is not installed in selected Codex home")


def _bash_hook(plugin: dict[str, Any]) -> tuple[str, float]:
    hooks = [
        hook
        for group in plugin.get("hooks", {}).get("PreToolUse", [])
        if group.get("matcher") == "Bash"
        for hook in group.get("hooks", [])
    ]
    if len(hooks) != 1:
        _fatal(f"expected exactly one Escapement Bash hook, found {len(hooks)}")
    command = hooks[0].get("command")
    if not isinstance(command, str) or "codex_pretool_dispatch.py" not in command:
        _fatal("the sole Escapement Bash hook is not the dispatcher")
    timeout = hooks[0].get("timeout")
    if not isinstance(timeout, (int, float)) or timeout <= 0:
        _fatal("Escapement Bash hook has no positive host timeout")
    return command, float(timeout)


def _command_argv(command: str, plugin_root: Path) -> list[str]:
    return shlex.split(command.replace("${PLUGIN_ROOT}", str(plugin_root)))


def _declared_gates(
    argv: list[str], plugin_root: Path
) -> list[tuple[Path, float]]:
    gates = []
    timeouts = []
    for index, token in enumerate(argv[:-1]):
        if token == "--gate":
            relative = Path(argv[index + 1])
            if relative.is_absolute() or ".." in relative.parts:
                _fatal(f"dispatcher gate is outside plugin root: {relative}")
            gate = (plugin_root / relative).resolve()
            try:
                gate.relative_to(plugin_root.resolve())
            except ValueError:
                _fatal(f"dispatcher gate is outside plugin root: {relative}")
            if not gate.is_file():
                _fatal(f"dispatcher gate is missing: {relative}")
            gates.append(gate)
        elif token == "--gate-timeout":
            try:
                timeout = float(argv[index + 1])
            except ValueError:
                _fatal(f"dispatcher gate timeout is invalid: {argv[index + 1]}")
            if timeout <= 0:
                _fatal("dispatcher gate timeouts must be positive")
            timeouts.append(timeout)
    if not gates:
        _fatal("dispatcher declares no gates")
    if len(timeouts) != len(gates):
        _fatal("dispatcher must declare one timeout per gate")
    return list(zip(gates, timeouts, strict=True))


def _run_probe(argv: list[str], cwd: Path, deadline: float) -> dict[str, Any]:
    payload = {
        "hook_event_name": "PreToolUse",
        "tool_name": "Bash",
        "tool_input": {"command": "pwd"},
        "cwd": str(cwd),
    }
    result = subprocess.run(
        argv,
        input=json.dumps(payload),
        text=True,
        capture_output=True,
        cwd=cwd,
        check=False,
        timeout=deadline,
    )
    if result.returncode != 0:
        _fatal(f"installed dispatcher probe failed: {result.stderr.strip()}")
    try:
        output = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        _fatal(f"installed dispatcher emitted invalid JSON: {exc}")
    if not isinstance(output, dict):
        _fatal("installed dispatcher emitted a non-object result")
    message = output.get("systemMessage", "")
    if isinstance(message, str) and "Escapement gate " in message and " failed:" in message:
        _fatal(f"installed dispatcher reported a gate failure: {message}")
    return output


def _verify_public_output(dispatcher: Path, deadline: float) -> None:
    with tempfile.TemporaryDirectory(prefix="escapement-codex-probe-") as raw:
        root = Path(raw) / "plugin"
        hooks = root / "claude" / "hooks"
        hooks.mkdir(parents=True)
        shutil.copy2(dispatcher, hooks / dispatcher.name)
        probe = hooks / "public_output_probe.py"
        probe.write_text(
            "import json, sys\n"
            "json.load(sys.stdin)\n"
            "print(json.dumps({'hookSpecificOutput': {"
            "'hookEventName': 'PreToolUse', "
            "'additionalContext': 'escapement-runtime-probe'}}))\n",
            encoding="utf-8",
        )
        output = _run_probe(
            [
                sys.executable,
                "-B",
                str(hooks / dispatcher.name),
                "--gate",
                "claude/hooks/public_output_probe.py",
            ],
            Path(raw),
            deadline,
        )
        context = output.get("hookSpecificOutput", {}).get("additionalContext")
        if context != "escapement-runtime-probe":
            _fatal("dispatcher lost advisory output from an installed-byte probe")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--codex-home", type=Path, required=True)
    parser.add_argument("--home", type=Path, default=Path.home())
    parser.add_argument("--plugin-root", type=Path)
    parser.add_argument("--require-installed", action="store_true")
    parser.add_argument("--codex-bin", default=os.environ.get("CODEX_BIN", "codex"))
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--deadline-seconds", type=float, default=8.0)
    args = parser.parse_args(argv)
    if args.concurrency < 1 or args.deadline_seconds <= 0:
        parser.error("concurrency and deadline must be positive")
    if args.plugin_root and args.require_installed:
        parser.error("choose --plugin-root or --require-installed")
    if args.require_installed:
        plugin_root = _installed_plugin_root(args.codex_home, args.codex_bin)
    elif args.plugin_root:
        plugin_root = args.plugin_root.resolve()
    else:
        parser.error("--plugin-root or --require-installed is required")

    hook_path = plugin_root / "hooks" / "hooks.json"
    plugin = json.loads(hook_path.read_text(encoding="utf-8"))
    command, host_timeout = _bash_hook(plugin)
    command_argv = _command_argv(command, plugin_root)
    gates = _declared_gates(command_argv, plugin_root)
    serial_budget = sum(timeout for _gate, timeout in gates) + len(gates)
    if host_timeout < serial_budget:
        _fatal(
            f"host timeout {host_timeout:g}s is below serial gate budget "
            f"{serial_budget:g}s"
        )
    dispatcher = Path(command_argv[2]).resolve()
    if not dispatcher.is_file():
        _fatal(f"installed dispatcher is missing: {dispatcher}")

    global_path = args.codex_home / "hooks.json"
    if global_path.is_file():
        global_hooks = json.loads(global_path.read_text(encoding="utf-8"))
        owned = plugin_owned_gate_scripts(plugin)
        if prune_hooks(
            global_hooks,
            owned,
            codex_home=args.codex_home,
            home=args.home,
        ) != global_hooks:
            _fatal("legacy global overlap remains in Codex hooks")

    _verify_public_output(dispatcher, args.deadline_seconds)
    workspace = args.home
    with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        futures = [
            pool.submit(
                _run_probe,
                command_argv,
                workspace,
                args.deadline_seconds,
            )
            for _ in range(args.concurrency)
        ]
        for future in futures:
            future.result()

    print(f"OK: one Escapement Bash hook declares {len(gates)} in-process gate(s)")
    print(f"OK: {args.concurrency} concurrent installed dispatcher probes passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
