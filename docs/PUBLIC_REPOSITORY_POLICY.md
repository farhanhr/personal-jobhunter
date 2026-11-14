# Public Repository and Local Data Policy

## Principle

Treat every committed file, Git object, branch, release asset, issue attachment, pull request comment, and CI log as public. Do not assume that deleting a file from the latest commit erases it from Git history.

## Publish (safe by default after review)

- Application source code and generic configuration templates.
- Generic architecture, product requirements, ADRs, and learning documentation.
- Original project-specific Agent Skills written for this repository.
- Test code and synthetic fixtures.
- `.env.example` and `config/search.example.yaml` containing placeholders only.
- CI, lint, test, and security configuration.
- Documentation links to official APIs and upstream repositories.

## Keep local / do not commit

- `.env`, `.env.local`, provider API keys, model-provider tokens, and local machine settings.
- Google OAuth client credential files, client secrets, access tokens, refresh tokens, and token caches.
- Service-account JSON, private keys, certificates, browser cookies, browser profiles, and session stores.
- Actual Google Spreadsheet ID and local Google account/email configuration. A spreadsheet ID is not itself an authentication secret, but it identifies a personal data document and belongs in local configuration.
- `config/search.local.yaml`, including the user's real three target roles, preferred cities, role aliases, and exclusions.
- SQLite database files, cache, run logs, sync outbox, downloaded job data, CSV exports, and screenshots from personal accounts.
- Real CVs, job application documents, real LinkedIn post text where it contains personal information, real HR/recruiter email addresses, and private Gmail messages.
- Any data copied from a personal Google Sheet or account.

## Never commit

- Passwords, API keys, OAuth client credentials, access/refresh tokens, private keys, session cookies, browser authentication state, or recovery codes.
- Real personal data disguised as a test fixture.
- Third-party skill packs or code without checking the exact upstream license and attribution obligations.

## Safe fixture rules

- Use reserved domains such as `example.test` and synthetic identities.
- Never use a real person's name, email, telephone number, personal LinkedIn URL, or actual recruiter contact as fixture data.
- Use a fake LinkedIn URL only as a non-resolving example, such as `https://www.linkedin.com/posts/example-placeholder`.
- Do not commit captured job data just to demonstrate the UI; create synthetic examples.

## `.gitignore` is not a security boundary

`.gitignore` prevents selected untracked files from being added accidentally. It does not untrack a file already committed, erase history, or stop a developer from force-adding a file.

Before every push:

```bash
git status --short
git diff --cached --stat
git diff --cached
gitleaks git .
```

Review every staged file. If a secret was committed or pushed:

1. Revoke/rotate the credential immediately at its issuing provider.
2. Remove it from the working tree and commit a safe replacement.
3. Assess and clean Git history if appropriate; a new deletion commit alone is insufficient.
4. Review logs, issues, artifacts, and releases where the value may have been copied.
5. Do not paste the leaked value into a public issue or chat.

## GitHub repository security settings

- Enable Dependabot alerts and security updates where available.
- Confirm public-repository secret scanning is active; GitHub documents automatic secret scanning for public repositories.
- Enable push protection if offered for the repository/account. Do not rely on it as the only control.
- Protect the default branch and require CI checks before merging when pull requests are used.
- Do not publish CI logs or artifacts containing environment values.

## License decision

Public visibility is not the same as an open-source license. Without a license, default copyright restrictions still apply to reuse. Recommended default if the owner wants broad reuse: MIT. Apache-2.0 is a stronger alternative when an explicit patent grant is desired. The final `LICENSE` file must name the actual copyright holder; do not publish a placeholder license or copy an upstream license without checking compatibility.

## External data and privacy

The application is personal/local-first, but personal data can still be sent to Google APIs or an optional LLM provider. Before enabling an integration, document the exact data transmitted, OAuth scopes, retention behavior, and whether the data is sent to a third party. Do not transmit an entire CV or raw email body to an LLM by default.
