"""How a hook speaks to a host.

One owner, because getting it wrong is silent. A hook that denies in the wrong
shape is deployed, fires, computes the right verdict, and changes nothing --
which is what shipped in #205 and again in #208.

Captured behavior, all three from live sessions rather than from docs:

  deny      `hookSpecificOutput.permissionDecision` with status 0.

            Not "either works on Claude". Emitting the JSON decision AND
            exiting non-zero is a contradictory double-signal: exit 2 is the
            legacy stderr-feedback path, so Claude reported "PreToolUse:Edit
            hook error: No stderr output" and the agent never saw the limit or
            the waiver escape (escapement-uk9i). Codex separately discards a
            hook that exits non-zero. The envelope with status 0 is the only
            thing that works on both, and the Pi extension reads the same
            field off the dispatcher.

  advisory  Codex does NOT surface a hook's top-level `systemMessage` to the
            model. A probe hook emitting both markers was asked which reached
            the model; the answer named only the one sent as
            `hookSpecificOutput.additionalContext`. Claude and the Pi
            extension read `systemMessage`. So: send both, and neither host
            loses the message.

Sending both channels is not belt-and-braces about one host's behavior -- it
is two hosts with two different names for the same channel.
"""

from __future__ import annotations


def deny(reason: str) -> dict:
    """A blocking verdict, in the shape every host honors."""
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }


def advisory(message: str) -> dict:
    """A non-blocking message, on both names the hosts use for it.

    `systemMessage` reaches Claude and the Pi extension's diagnostics;
    `additionalContext` is the only one Codex passes to the model.
    """
    return {
        "systemMessage": message,
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "additionalContext": message,
        },
    }
