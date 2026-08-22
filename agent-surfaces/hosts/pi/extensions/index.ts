import { spawn } from "node:child_process";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";

type Handler = (event: any, context: any) => any;
type PiAPI = { on(event: string, handler: Handler): void };
type Gate = { id: string; source: string; timeout_seconds: number };
type Inventory = { version: number; dispatcher: string; gates: Gate[] };

const pluginRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const inventory: Inventory = JSON.parse(
  readFileSync(resolve(pluginRoot, "gates.json"), "utf8"),
);
const instructions = readFileSync(resolve(pluginRoot, "PI.md"), "utf8");

if (inventory.version !== 1 || !inventory.dispatcher || !inventory.gates.length) {
  throw new Error("Escapement Pi gate inventory is missing or invalid");
}

function runDispatcher(payload: Record<string, unknown>): Promise<Record<string, any>> {
  const args = ["-B", resolve(pluginRoot, inventory.dispatcher)];
  for (const gate of inventory.gates) {
    args.push("--gate", gate.source, "--gate-timeout", String(gate.timeout_seconds));
  }
  const deadlineMs = inventory.gates.reduce(
    (total, gate) => total + (gate.timeout_seconds + 1) * 1000,
    0,
  );

  return new Promise((resolveResult, reject) => {
    const child = spawn("python3", args, { stdio: ["pipe", "pipe", "pipe"] });
    let stdout = "";
    let stderr = "";
    let settled = false;
    const finish = (callback: () => void) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      callback();
    };
    const timer = setTimeout(() => {
      child.kill("SIGKILL");
      finish(() => reject(new Error("Escapement Pi dispatcher timed out")));
    }, deadlineMs);

    child.stdout.setEncoding("utf8");
    child.stderr.setEncoding("utf8");
    child.stdout.on("data", (chunk) => { stdout += chunk; });
    child.stderr.on("data", (chunk) => { stderr += chunk; });
    child.on("error", (error) => finish(() => reject(error)));
    child.on("close", (code) => finish(() => {
      if (code !== 0) {
        reject(new Error(stderr.trim() || `Escapement Pi dispatcher exited ${code}`));
        return;
      }
      try {
        const result = JSON.parse(stdout);
        if (!result || typeof result !== "object" || Array.isArray(result)) {
          throw new Error("dispatcher emitted a non-object result");
        }
        resolveResult(result);
      } catch (error) {
        reject(new Error(`Escapement Pi dispatcher returned invalid JSON: ${error}`));
      }
    }));
    child.stdin.end(JSON.stringify(payload));
  });
}

export default function escapementPi(pi: PiAPI): void {
  pi.on("before_agent_start", (event) => ({
    systemPrompt: `${event.systemPrompt}\n\n${instructions}`,
  }));

  pi.on("tool_call", async (event, context) => {
    if (event.toolName !== "bash") return;
    const command = event.input?.command;
    if (typeof command !== "string") {
      return { block: true, reason: "Escapement received an invalid Pi Bash payload" };
    }

    try {
      const result = await runDispatcher({
        session_id: event.toolCallId,
        cwd: context.cwd,
        hook_event_name: "PreToolUse",
        tool_name: "Bash",
        tool_input: { command },
      });
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
