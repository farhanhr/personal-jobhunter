---
name: public-repository-security
description: Use before staging, committing, pushing, releasing, adding fixtures/screenshots/logs, integrating credentials, or changing repository visibility and publication settings.
---

# Public Repository Security

Assume every tracked byte is public. Read `docs/PUBLIC_REPOSITORY_POLICY.md` and `SECURITY.md`.

## Never commit

- `.env` files, API keys, model-provider credentials, OAuth client credentials, access/refresh tokens, service account JSON, private keys/certificates, cookies/session state, browser profiles, SQLite DBs, logs with personal data, real job exports, CVs, recruiter contact lists, or real email notifications.
- The real spreadsheet ID or the user's local search roles/configuration.
- Real names/emails/phones in test fixtures. Use `.test` domains and synthetic values.

## Before any commit/push

1. Review `git status --short`.
2. Review all staged paths and full staged diff.
3. Run the skill validator and secret scanner.
4. Confirm ignored local files are not already tracked (`git ls-files`).
5. Check that docs and screenshots do not disclose personal data.
6. Require explicit user approval before publishing/pushing.

## If a secret is found

- Do not print it into chat, logs, or issues.
- Stop the operation.
- If already exposed, advise immediate revocation/rotation before history cleanup.
- A deletion commit is not sufficient to remove a leaked secret from history.

## External skills

Do not vendor an upstream skill without checking its exact source, version, license, scripts and permission needs. Keep attribution and license notices where required.
