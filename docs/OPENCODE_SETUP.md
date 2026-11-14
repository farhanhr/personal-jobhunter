# OpenCode Setup for This Repository

## 1. Confirm installed version

```bash
opencode --version
```

The committed `opencode.jsonc` uses the current OpenCode V2 configuration shape (`permissions`, V2 permission actions such as `shell`, and `mcp.servers`). The current OpenCode documentation distinguishes V2 from V1. If the installed binary is V1, do not load this config unchanged; update OpenCode or request a V1-compatible config.

## 2. Read the safety configuration

Open `opencode.jsonc` before starting OpenCode. It:

- asks before shell commands by default;
- allows routine `git status`, `git diff`, and `git log` inspection;
- blocks destructive reset/clean/remove patterns;
- denies reading `.env` files and allows `.env.example`;
- denies agent reads/edits of local credentials and data folders;
- asks before accessing files outside the project directory;
- configures Context7 as a remote MCP server using its documented endpoint.

These are guardrails, not a full operating-system sandbox. Keep credentials out of the project tree and review approvals.

## 3. Open the project

From the repository root:

```bash
opencode
```

OpenCode reads root `AGENTS.md`, discovers project skills under `.agents/skills/`, and discovers custom project agents under `.opencode/agents/`.

## 4. Verify the skills

Ask OpenCode to list the discovered skills and explain when each should activate. Do not ask it to implement application features yet.

Run the repository validator independently:

```bash
python3 scripts/validate_skills.py
```

## 5. Verify custom agents

Ask the primary agent to describe the `architect`, `reviewer`, and `learning-mentor` subagents. Confirm reviewer and mentor are read-only and that no provider/model has been hardcoded in their profiles.

## 6. Verify Context7

```bash
opencode mcp list
```

If authentication is requested, complete the official OpenCode MCP authentication flow. Do not paste an API key into `opencode.jsonc`. Context7 can be omitted if you do not want a remote documentation tool; the project should remain buildable without it.

## 7. Optional third-party skills

Follow `docs/AGENT_SKILL_SOURCING.md`. Install only reviewed skills. Inspect the complete diff before committing them.

## 8. Optional Superpowers plugin

Deferred. Install only after confirming OpenCode V2 >= 2.0.4 and reading the current upstream OpenCode-specific guide. It is not required for the initial P1 harness.

## 9. Keep model providers local

Do not commit provider names, API keys, custom endpoints containing tokens, or model-provider credentials. Configure providers using the supported OpenCode local login/configuration mechanism. The repository config intentionally does not select a specific model.
