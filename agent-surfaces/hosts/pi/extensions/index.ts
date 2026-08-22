import { spawn } from "node:child_process";
import { readFileSync, realpathSync, statSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, isAbsolute, relative, resolve, sep } from "node:path";

type Handler = (event: any, context: any) => any;
type PiAPI = {
  on(event: string, handler: Handler): void;
  sendMessage(message: Record<string, unknown>, options?: Record<string, unknown>): void;
};
type Gate = { id: string; source: string; timeout_seconds: number };
type HookOutput = {
  hookEventName: "PreToolUse";
  permissionDecision?: "allow" | "ask" | "deny";
  permissionDecisionReason?: string;
  additionalContext?: string;
};
type DispatcherResponse = { hookSpecificOutput?: HookOutput; systemMessage?: string };
type Runtime = { dispatcherPath: string; gates: Gate[]; instructions: string };

const pluginRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const MAX_OUTPUT_BYTES = 1_048_576;

function fail(message: string): never {
  throw new Error(message);
}

function confinedFile(relativePath: string): string {
  if (isAbsolute(relativePath) || relativePath.split(/[\\/]/).some((part) => part === "..")) {
    return fail("dispatcher path escapes the Pi package root");
  }
  const root = realpathSync(pluginRoot);
  const candidate = realpathSync(resolve(root, relativePath));
  const within = relative(root, candidate);
  if (within === ".." || within.slice(0, 3) === `..${sep}` || isAbsolute(within)) {
    return fail("dispatcher path escapes the Pi package root");
  }
  if (!statSync(candidate).isFile()) return fail("dispatcher path is not a regular file");
  return candidate;
}

function loadRuntime(): Runtime {
  const parsed = JSON.parse(readFileSync(resolve(pluginRoot, "gates.json"), "utf8"));
  if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
    return fail("gate inventory must be an object");
  }
  if (parsed.version !== 1 || typeof parsed.dispatcher !== "string") {
    return fail("gate inventory version or dispatcher is invalid");
  }
  if (!Array.isArray(parsed.gates) || parsed.gates.length === 0) {
    return fail("gate inventory must contain gates");
  }
  for (const gate of parsed.gates) {
    if (
      !gate || typeof gate !== "object" || Array.isArray(gate)
      || typeof gate.id !== "string" || typeof gate.source !== "string"
      || typeof gate.timeout_seconds !== "number"
      || !Number.isFinite(gate.timeout_seconds) || gate.timeout_seconds <= 0
    ) {
      return fail("gate inventory contains an invalid gate");
    }
  }
  return {
    dispatcherPath: confinedFile(parsed.dispatcher),
    gates: parsed.gates,
    instructions: readFileSync(resolve(pluginRoot, "PI.md"), "utf8"),
  };
}

function parseDispatcherResponse(stdout: string): DispatcherResponse {
  const result = JSON.parse(stdout);
  if (!result || typeof result !== "object" || Array.isArray(result)) {
    return fail("dispatcher result must be an object");
  }
  const allowedTop = new Set(["hookSpecificOutput", "systemMessage"]);
  if (Object.keys(result).some((key) => !allowedTop.has(key))) {
    return fail("dispatcher result contains unknown fields");
  }
  if (result.systemMessage !== undefined && typeof result.systemMessage !== "string") {
    return fail("dispatcher systemMessage must be a string");
  }
  const hook = result.hookSpecificOutput;
  if (hook !== undefined) {
    if (!hook || typeof hook !== "object" || Array.isArray(hook)) {
      return fail("dispatcher hookSpecificOutput must be an object");
    }
    const allowedHook = new Set([
      "hookEventName", "permissionDecision", "permissionDecisionReason", "additionalContext",
    ]);
    if (Object.keys(hook).some((key) => !allowedHook.has(key))) {
      return fail("dispatcher hookSpecificOutput contains unknown fields");
    }
    if (hook.hookEventName !== "PreToolUse") return fail("dispatcher hook event is invalid");
    if (
      hook.permissionDecision !== undefined
      && hook.permissionDecision !== "allow"
      && hook.permissionDecision !== "ask"
      && hook.permissionDecision !== "deny"
    ) {
      return fail("dispatcher permission decision is invalid");
    }
    for (const field of ["permissionDecisionReason", "additionalContext"]) {
      if (hook[field] !== undefined && typeof hook[field] !== "string") {
        return fail(`dispatcher ${field} must be a string`);
      }
    }
  }
  return result;
}

function runDispatcher(runtime: Runtime, payload: Record<string, unknown>): Promise<DispatcherResponse> {
  const args = ["-B", runtime.dispatcherPath];
  for (const gate of runtime.gates) {
    args.push("--gate", gate.source, "--gate-timeout", String(gate.timeout_seconds));
  }
  const deadlineMs = runtime.gates.reduce(
    (total, gate) => total + (gate.timeout_seconds + 1) * 1000,
    0,
  );

  return new Promise((resolveResult, reject) => {
    const child = spawn("python3", args, { stdio: ["pipe", "pipe", "pipe"] });
    const stdout: Buffer[] = [];
    const stderr: Buffer[] = [];
    let outputBytes = 0;
    let settled = false;
    const finish = (callback: () => void) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      callback();
    };
    const collect = (target: Buffer[], chunk: Buffer) => {
      outputBytes += chunk.length;
      if (outputBytes > MAX_OUTPUT_BYTES) {
        child.kill("SIGKILL");
        finish(() => reject(new Error("dispatcher output exceeded 1048576 bytes")));
        return;
      }
      target.push(chunk);
    };
    const timer = setTimeout(() => {
      child.kill("SIGKILL");
      finish(() => reject(new Error("Escapement Pi dispatcher timed out")));
    }, deadlineMs);

    child.stdout.on("data", (chunk: Buffer) => collect(stdout, chunk));
    child.stderr.on("data", (chunk: Buffer) => collect(stderr, chunk));
    child.on("error", (error) => finish(() => reject(error)));
    child.on("close", (code) => finish(() => {
      const renderedError = Buffer.concat(stderr).toString("utf8").trim();
      if (code !== 0) {
        reject(new Error(renderedError || `Escapement Pi dispatcher exited ${code}`));
        return;
      }
      try {
        resolveResult(parseDispatcherResponse(Buffer.concat(stdout).toString("utf8")));
      } catch (error) {
        reject(new Error(`Escapement Pi dispatcher returned invalid JSON: ${error}`));
      }
    }));
    child.stdin.end(JSON.stringify(payload));
  });
}

function surfaceDiagnostics(pi: PiAPI, result: DispatcherResponse): void {
  const messages = [result.systemMessage, result.hookSpecificOutput?.additionalContext]
    .filter((message): message is string => Boolean(message));
  if (messages.length === 0) return;
  pi.sendMessage(
    { customType: "escapement", content: messages.join("\n\n"), display: true },
    { deliverAs: "followUp", triggerTurn: false },
  );
}

export default function escapementPi(pi: PiAPI): void {
  let runtime: Runtime | Error;
  try {
    runtime = loadRuntime();
  } catch (error) {
    runtime = error instanceof Error ? error : new Error(String(error));
  }

  pi.on("before_agent_start", (event) => ({
    systemPrompt: runtime instanceof Error
      ? `${event.systemPrompt}\n\nEscapement Pi configuration error: ${runtime.message}`
      : `${event.systemPrompt}\n\n${runtime.instructions}`,
  }));

  pi.on("tool_call", async (event, context) => {
    if (event.toolName !== "bash") return;
    if (runtime instanceof Error) {
      return { block: true, reason: `Escapement Pi configuration error: ${runtime.message}` };
    }
    const command = event.input?.command;
    if (typeof command !== "string") {
      return { block: true, reason: "Escapement received an invalid Pi Bash payload" };
    }

    try {
      const result = await runDispatcher(runtime, {
        session_id: event.toolCallId,
        cwd: context.cwd,
        hook_event_name: "PreToolUse",
        tool_name: "Bash",
        tool_input: { command },
      });
      surfaceDiagnostics(pi, result);
      const hook = result.hookSpecificOutput;
      const decision = hook?.permissionDecision;
      if (decision === "deny" || decision === "ask") {
        return {
          block: true,
          reason: hook.permissionDecisionReason || "Escapement blocked this Bash call",
        };
      }
    } catch (error) {
      return { block: true, reason: `Escapement Pi adapter error: ${error}` };
    }
  });
}
