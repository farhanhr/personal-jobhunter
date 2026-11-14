# P1 Setup Checklist (Ubuntu / local development)

This pack prepares the repository. It does not install software on your laptop or create a GitHub repository automatically. Follow the steps below in your project directory.

## Step 1 — Extract the pack

Download `personal_job_finder_p1_harness_pack.zip` and extract it into a new folder that will become the project root. The archive contains the P0 documents under `docs/p0/`.

## Step 2 — Check your tools

Run these commands and inspect the versions:

```bash
git --version
python3 --version
node --version
npm --version
opencode --version
```

Do not install a guessed version blindly. The frontend and backend dependency versions will be pinned during P2. The committed `opencode.jsonc` targets the current OpenCode V2 config format. If the installed CLI is V1, stop and update OpenCode or request a V1-compatible config; do not mix V1/V2 syntax.

## Step 3 — Initialize Git locally

From the project root:

```bash
git init
git status --short
```

Before creating a public remote, complete `docs/PUBLICATION_CHECKLIST.md` and decide the license. Do not push yet if the license holder/details are unresolved.

## Step 4 — Validate project skills

```bash
python3 scripts/validate_skills.py
```

Expected result: 12 project-specific Agent Skills validated. If this fails, fix the frontmatter/folder mismatch before opening an AI coding session.

## Step 5 — Install pre-commit

Install pre-commit using a trusted package manager, for example:

```bash
uv tool install pre-commit
```

Then, from the project root:

```bash
pre-commit install
pre-commit run --all-files
```

The configured hooks check whitespace, YAML/TOML syntax, merge conflicts, private keys, large files, and secrets using Gitleaks. Hook versions are pinned in `.pre-commit-config.yaml`; update intentionally and review version changes.

## Step 6 — Configure OpenCode

Read `opencode.jsonc` and `docs/OPENCODE_SETUP.md` first. Start OpenCode from the repository root:

```bash
opencode
```

The project config adds Context7 as a remote MCP server. Verify it with:

```bash
opencode mcp list
```

Complete OAuth only through OpenCode's supported authentication flow if requested. Do not paste credentials into the repository.

## Step 7 — Verify agent and skill discovery

Ask the OpenCode primary agent to:

1. Read `AGENTS.md`.
2. List available project skills and their trigger conditions.
3. Describe the `architect`, `reviewer`, and `learning-mentor` subagents.
4. Confirm the reviewer and learning mentor cannot edit files or run shell commands.
5. Summarize the P0 product invariants without implementing code.

If a skill is not discovered, verify its exact path: `.agents/skills/<skill-id>/SKILL.md`. OpenCode also supports `.opencode/skills/`; this project deliberately uses `.agents/skills/` for cross-agent portability.

## Step 8 — Optional upstream skills

Only after reading `docs/AGENT_SKILL_SOURCING.md`, install the two selected Vercel skills if they add value. Review all downloaded files and the Git diff. Do not install every skill in a repository.

Superpowers is optional and deferred. Confirm OpenCode V2 >= 2.0.4 and read its current OpenCode-specific guide before enabling the plugin.

## Step 9 — Prepare local-only settings

Copy `.env.example` to `.env` and `config/search.example.yaml` to `config/search.local.yaml` only when needed. Fill personal values locally. Confirm both files are ignored:

```bash
git check-ignore -v .env config/search.local.yaml
git status --short
```

Do not put real OAuth JSON files or tokens into the repository. Keep them in the ignored `credentials/` or `data/credentials/` directories.

## Step 10 — Public repo hygiene

Before first public push:

```bash
git ls-files
git status --short
gitleaks git .
```

Also inspect every staged diff. A clean Gitleaks scan does not guarantee that no personal data is present; manual review is mandatory.
