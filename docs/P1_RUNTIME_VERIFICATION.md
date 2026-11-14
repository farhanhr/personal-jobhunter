# P1 — OpenCode Runtime Verification

Run these checks from the repository root after confirming OpenCode V2 is installed.

## 1. Confirm configuration sources and discovered agents

```bash
opencode --version
opencode debug config
opencode debug agents
```

Expected:
- OpenCode reports V2 (the project owner currently has v2.2.7).
- The project `opencode.jsonc` appears as a loaded configuration source.
- `architect`, `reviewer`, and `learning-mentor` appear as custom project agents.
- Built-in `build` and `plan` agents remain available.

## 2. Verify MCP

```bash
opencode mcp list
```

Expected:
- `context7` appears in the list.
- It is connected, or explicitly reports that authentication is needed.

If authentication is needed, start the TUI with `opencode`, run `/mcps`, select Context7, and follow the authentication flow. Never put an MCP API key into the committed project config.

## 3. Verify skill discovery in the TUI

Start OpenCode from the repository root:

```bash
opencode
```

Then ask the primary agent to perform a read-only audit:

> Do not edit files and do not execute shell commands. Read AGENTS.md and the P0 specifications. List the 12 project skills discovered under `.agents/skills/` by exact skill ID and describe when each applies. Explicitly load `architecture-planning`, `public-repository-security`, and `testing-and-verification` using the skill tool, then summarize one important instruction from each. List the three project subagents and explain their configured restrictions. Do not claim a skill or agent is available unless you can see it in OpenCode runtime discovery.

Expected:
- All 12 skill IDs are visible to the primary agent.
- The three named skills can be loaded explicitly.
- The three custom subagents are recognized.
- No files are modified.

## 4. Verify read-only subagents

Ask the primary agent:

> Invoke the `architect` subagent to review the P0 system architecture and report the approved boundaries. Then invoke `reviewer` to review the specification for conflicts with AGENTS.md. Do not edit files. Report any permission or discovery error verbatim.

Expected:
- Both subagents can read the repository and respond.
- They do not run shell commands or modify files.

Then test Learning Mentor:

> Invoke `learning-mentor` to explain Modular Monolith, Adapter Pattern, and Idempotency using this project as examples. Do not modify files or execute shell commands.

Expected:
- Beginner-friendly explanation in Indonesian.
- No file changes or shell execution.

## 5. Security tightening before real secrets are introduced

Review `opencode.jsonc`. Prefer denying reads of `.env` and `.env.*` rather than merely asking, with a later exception that allows `.env.example`. Keep credentials, secrets, databases, and local data paths denied. Do not store actual credentials in the repository.

## 6. Evidence to record

Record in a local checklist (do not commit personal machine paths or secrets):
- OpenCode version.
- Whether project config loads without schema errors.
- Whether 12 skills are discovered.
- Whether all three custom subagents are discovered and callable.
- Context7 MCP state (connected / needs auth / failed).
- Whether read-only restrictions behaved as expected.

Do not proceed to P2 repository initialization until the project config and agent discovery checks pass.
