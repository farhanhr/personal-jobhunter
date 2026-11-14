# P1 Runtime Hardening

**Status:** Required before P2/public repository setup

## 1. What runtime verification proved

- OpenCode v2.2.7 was installed.
- 12 project skills under `.agents/skills/` were discovered; 3 were explicitly loaded.
- `architect`, `reviewer`, and `learning-mentor` were registered as project subagents.
- Architect review was invoked successfully.
- Context7 was registered but `opencode mcp list` reported DNS `EAI_AGAIN` for `mcp.context7.com`.

It did not prove Context7 connectivity, and it did not prove all three subagents' denied operations at runtime.

## 2. Permission findings

OpenCode V2 evaluates permission rules in order; the last matching rule wins. Agent rules are appended after global rules, and a custom subagent uses its own permission rules rather than a restricted subset of the parent. Consult the current OpenCode Permissions documentation before altering configuration.

The runtime dump showed broad `* allow` rules before narrower rules. The three project subagents deny edit/shell/subagent, but their access to web tools should also be explicitly denied if the agents are intended to review only local project content.

The project config used `ask` for `.env` reads. Change it to explicit `deny`, then place the `.env.example` allow exception after the `.env.*` deny. Apply the hardened per-agent files supplied in `patches/agents/`.

## 3. Expected subagent capability matrix

| Capability | Architect | Reviewer | Learning Mentor |
|---|---:|---:|---:|
| Read project files | Allow | Allow | Allow |
| Glob/grep | Allow | Allow | Allow |
| Load project skills | Allow | Allow | Allow |
| Edit/write/patch | Deny | Deny | Deny |
| Shell | Deny | Deny | Deny |
| Nested subagents | Deny | Deny | Deny |
| Web search/fetch | Deny | Deny | Deny |
| Browser | Deny | Deny | Deny |
| External directory | Deny | Deny | Deny |
| Read `.env`/credentials/local data | Deny | Deny | Deny |

Reviewer has no shell access. The primary agent must pass the relevant diff as context when invoking the reviewer.

## 4. Context7 DNS diagnosis

`EAI_AGAIN` is a DNS resolution failure, not evidence of a bad MCP protocol setting or a missing API key. Do not rotate endpoints or change protocol versions to fix a DNS error.

Run:

```bash
getent hosts mcp.context7.com
resolvectl query mcp.context7.com 2>/dev/null || true
curl -I --connect-timeout 5 https://mcp.context7.com/mcp
opencode mcp list
```

Interpretation:
- If DNS commands fail, resolve the local DNS/network issue first.
- If DNS resolves but `curl` fails, inspect connectivity/firewall/proxy.
- If the endpoint responds but OpenCode reports `needs authentication`, follow the `/mcps` or `opencode mcp auth` flow.
- Context7's API key is recommended for higher rate limits; it is not a fix for DNS resolution.

Context7 is useful but not a blocker for P2. If unavailable, use official library documentation directly and retry MCP later.

## 5. Do not expose secrets during tests

Do not create a real `.env` just to test permissions. Use a synthetic file with a fake value in a temporary fixture path, verify `read` is denied, then delete it yourself. Never paste actual API keys, OAuth tokens, or spreadsheet IDs into chat or issues.
