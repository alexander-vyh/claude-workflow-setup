---
name: "OPSX: Apply"
description: "Implement tasks from an OpenSpec change (Experimental)"
category: "Workflow"
tags: [workflow, artifacts, experimental]
---

Implement tasks from an OpenSpec change.

**Input**: Optionally specify a change name. If omitted, check if it can be inferred from conversation context. If vague or ambiguous you MUST prompt for available changes.

**Steps**

1. **Select the change**

   If a name is provided, use it. Otherwise:
   - Infer from conversation context if the user mentioned a change
   - Auto-select if only one active change exists
   - If ambiguous, run `openspec list --json` to get available changes and use the **AskUserQuestion tool** to let the user select.

   Always announce: "Using change: <name>" and how to override.

2. **Check status to understand the schema**
   ```bash
   openspec status --change "<name>" --json
   ```
   Parse the JSON to understand:
   - `schemaName`: The workflow being used (e.g., "spec-driven")
   - Which artifact contains the tasks (typically "tasks" for spec-driven, check status for others)

3. **Get apply instructions**

   ```bash
   openspec instructions apply --change "<name>" --json
   ```

   This returns:
   - Context file paths (varies by schema - could be proposal/specs/design/tasks or spec/tests/implementation/docs)
   - Progress (total, complete, remaining)
   - Task list with status
   - Dynamic instruction based on current state

   **Handle states:**
   - If `state: "blocked"` (missing artifacts): show which artifacts are missing and suggest creating them before applying
   - If `state: "all_done"`: congratulate, suggest archive
   - Otherwise: proceed to implementation

4. **Read context files**

   Read the files listed in `contextFiles` from the apply instructions output.
   The files depend on the schema being used:
   - **spec-driven**: proposal, specs, design, tasks
   - Other schemas: follow the contextFiles from CLI output

5. **Show current progress**

   Display:
   - Schema being used
   - Progress: "N/M tasks complete"
   - Remaining tasks overview
   - Dynamic instruction from CLI

6. **Implement tasks (loop until done or blocked)**

   For each pending task:
   - Show which task is being worked on
   - Ensure there is a bead for the project work before implementation; create
     or update one if needed
   - Make the code changes required
   - Keep changes minimal and focused
   - Mark the OpenSpec task complete in `tasks.md` only as artifact progress:
     `- [ ]` → `- [x]`
   - Update or close the relevant bead for actual project tracking
   - Continue to next task

   **Pause if:**
   - Task is unclear → ask for clarification
   - Implementation reveals a design issue → suggest updating artifacts
   - Error or blocker encountered → report and wait for guidance
   - User interrupts

7. **On completion or pause, show status**

   Display:
   - Tasks completed this session
   - Overall progress: "N/M tasks complete"
   - Bead IDs updated or closed for the completed work
   - If all done: suggest archive
   - If paused: explain why and wait for guidance

**Output During Implementation**

```
## Implementing: <change-name> (schema: <schema-name>)

Working on task 3/7: <task description>
[...implementation happening...]
✓ Task complete

Working on task 4/7: <task description>
[...implementation happening...]
✓ Task complete
```

**Output On Completion**

```
## Implementation Complete

**Change:** <change-name>
**Schema:** <schema-name>
**Progress:** 7/7 tasks complete ✓

### Completed This Session
- [x] Task 1
- [x] Task 2
...

All tasks complete! Ready to archive this change.
```

**Output On An Action-Local Consequential Block**

```
## Task Blocked; Independent Work Continuing

**Change:** <change-name>
**Schema:** <schema-name>
**Progress:** 4/7 tasks complete

### Issue Encountered
<description of the issue>

### Affected Work
<blocked task and dependents>

### Work Still Running
<independent ready tasks, verification, or repair continuing now>

**Options:**
1. <option 1>
2. <option 2>
3. Other approach

The decision is required only for the affected action. The rest of the delegated
change continues unless every remaining route depends on this same choice.
```

**Guardrails**
- Keep going through tasks until the complete delegated outcome is verified
- Always read context files before starting (from the apply instructions output)
- If a task is ambiguous, block only that task and its dependents; continue independent ready tasks
- If implementation reveals a causal design issue inside the delegated outcome, update the artifacts and continue; ask only for a consequential choice that changes intent, scope, privilege, audience, or irreversible effect
- Keep code changes minimal and scoped to each task
- Update task checkbox immediately after completing each task
- Treat `tasks.md` as artifact-state only — bead state is the authority for project tracking
- Repair errors and blockers that are causally necessary to the outcome. Ask only when every remaining authorized route depends on the same unresolved consequential choice
- Use contextFiles from CLI output, don't assume specific file names

**Fluid Workflow Integration**

This skill supports the "actions on a change" model:

- **Can be invoked anytime**: Before all artifacts are done (if tasks exist), after partial implementation, interleaved with other actions
- **Allows artifact updates**: If implementation reveals design issues, suggest updating artifacts - not phase-locked, work fluidly
