# Pi Adapter Implementation Plan

> **For Codex:** Execute this plan in order. Tests define the package contract before implementation.

**Goal:** Make the Escapement Git repository root directly installable by Pi, using the same neutral manifest, instructions, skills, and Python gate dispatcher as the Claude and Codex distributions.

**Architecture:** `tools/render_agent_surfaces.py` renders a root Pi package manifest, Pi instructions, and a Pi-ready gate inventory from `agent-surfaces/manifest.json`. A thin TypeScript extension translates Pi Bash calls into the existing dispatcher payload; it contains no workflow policy.

**Runtime:** Pi 0.84.2 extension API, Node built-ins, Python 3, pytest.

---

### Task 1: Lock the package and gate oracle

**Files:**
- Add: `tests/test_pi_adapter.py`
- Modify: `agent-surfaces/manifest.json`

1. Add failing tests proving the root `package.json` exposes the extension, shared skills, and `pi-package` keyword.
2. Add a negative control proving the generated gate inventory contains every and only Pi-ready Bash gate declared by the neutral manifest.
3. Add a static negative control rejecting per-gate process spawning or gate-specific policy in the extension.
4. Run `pytest -q tests/test_pi_adapter.py` and confirm it fails because the Pi surface does not exist.

### Task 2: Render the Pi package from the shared root

**Files:**
- Modify: `tools/render_agent_surfaces.py`
- Modify: `agent-surfaces/manifest.json`
- Generate: `package.json`
- Generate: `plugins/escapement-pi/PI.md`
- Generate: `plugins/escapement-pi/gates.json`

1. Add Pi as a document host and explicitly classify each hook as ready or unsupported.
2. Render package metadata, shared instructions, and the Pi-ready Bash gate inventory.
3. Treat `plugins/escapement-pi` as one renderer-owned publication unit.
4. Run the focused tests and `python3 -B tools/render_agent_surfaces.py --check`.

### Task 3: Add the thin Pi extension

**Files:**
- Add: `plugins/escapement-pi/extensions/index.ts`
- Modify: `tests/test_pi_adapter.py`

1. Add a behavioral harness that registers the extension against a fake Pi API and executes a real shared Python gate.
2. Prove a safe Bash command is allowed and a forbidden direct worktree-creation command is blocked.
3. Implement one dispatcher invocation per Bash tool call, with Pi-to-hook payload translation and fail-closed runtime errors.
4. Inject generated `PI.md` through `before_agent_start`; do not add a second policy implementation or background service.
5. Run `pytest -q tests/test_pi_adapter.py tests/test_codex_pretool_dispatch.py tests/test_agent_surfaces.py`.

### Task 4: Verify, land, and install

**Files:**
- Add: `scripts/verify_pi_package.py` only if the existing tests cannot express the installed-package check without duplication.

1. Run the renderer check and full relevant test suite.
2. Mutation-challenge the oracle and repair any test that permits a stale inventory, empty package, or per-gate spawn implementation.
3. Load/install the package with an isolated `PI_CODING_AGENT_DIR`; verify Pi lists the extension and shared skills.
4. Request code review and perform independent outcome verification.
5. Push `feat/pi-adapter-v2`, open the PR, merge after required checks, install from `git:github.com/alexander-vyh/escapement`, and repeat the package/runtime verification against merged `main`.
