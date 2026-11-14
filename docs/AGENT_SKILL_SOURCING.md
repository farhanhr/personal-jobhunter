# Agent Skill Sourcing and Adoption Policy

## Strategy

Use a small, reviewed set of skills. Prefer original project-specific instructions in `.agents/skills/`. Import third-party skills only when they add a capability not already covered, after reviewing their content, license, scripts, network behavior, and supported agent paths.

## Sources reviewed (12 November 2026)

| Source | License / status observed | Decision | Why |
|---|---|---|---|
| `vercel-labs/agent-skills` | Repository declares MIT | Adopt selectively | Useful React performance and web UI review guidance. Do not import the entire collection. |
| `obra/superpowers` | MIT; has an OpenCode V2 integration guide | Optional, deferred | Strong brainstorming, plan-writing, TDD, review and workflow methodology. It is a plugin, not just a portable SKILL.md drop-in. Check installed OpenCode version first. |
| `wshobson/agents` | Repository declares MIT; large multi-agent collection | Reference only | Useful examples and selected ideas; do not install hundreds of skills/plugins into a small single-user project. |
| `anthropics/skills` | Repository contains skills with different availability/licensing status; some document assets are source-available rather than open source | Reference only | Do not copy the entire repo or assume every nested skill has the same license. Check the exact skill folder. |

## Selected Vercel skills

Recommended optional additions:

- `vercel-react-best-practices`: use for React component implementation and review. Its guidance includes React and Next.js; ignore Next.js-specific rules because this project uses Vite SPA.
- `web-design-guidelines`: use as a UI accessibility/usability review aid.

After inspecting both skills in the upstream repo, install only these skills:

```bash
npx skills add vercel-labs/agent-skills \
  --skill vercel-react-best-practices \
  --skill web-design-guidelines \
  --agent opencode
```

Review the resulting diff before committing. The skills CLI may create agent-specific directories or symlinks; confirm the installation path and avoid duplicate IDs. Pin an upstream release/commit when the CLI supports it and record the resolved revision in the repository's skill inventory.

## Superpowers (optional, not enabled by default)

Superpowers offers a complete coding methodology, including brainstorming, writing plans, TDD, debugging, review and branch finishing. Its current OpenCode V2 guide states that OpenCode V2 requires version 2.0.4 or later and uses the `plugins` configuration key (not the V1 `plugin` key).

Do not install it until:

1. `opencode --version` is checked.
2. The current upstream `docs/README.opencode.md` is read.
3. The user approves adding a plugin to `opencode.jsonc`.
4. Potential overlap with this project's `AGENTS.md` and learning workflow is reviewed.

Do not blindly paste an old V1 plugin snippet into a V2 config.

## Review checklist for any external skill

- Is the upstream repository authentic and maintained?
- Is the exact skill file compatible with the Agent Skills format?
- Does the repository or nested directory have a usable license?
- Does the skill include scripts, binary assets, network calls, telemetry or shell commands?
- Does it request excessive permissions?
- Does it conflict with `AGENTS.md`, source policies, or local-only boundaries?
- Can it be replaced by a concise original project skill?
- Is the upstream revision pinned and recorded?

## Important

Skills are instructions, not a security sandbox. A malicious or careless skill can still recommend unsafe commands. Read new skill content before trusting it, and keep OpenCode permissions restrictive.
