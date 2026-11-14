# Public Publication Checklist

Complete before creating the public GitHub repository or pushing the first commit.

- [ ] Confirm repository name and public visibility.
- [ ] Choose a license. Recommended default: MIT if broad reuse is intended. Do not publish a placeholder.
- [ ] Review all P0/P1 documents for personal search criteria, email addresses, private URLs, account identifiers, and local paths.
- [ ] Confirm `config/search.local.yaml` is ignored and absent from Git staging.
- [ ] Confirm `.env` and all credentials/tokens are ignored and absent from staging.
- [ ] Confirm no SQLite DB, job exports, real LinkedIn post text, CV, or recruiter contacts are present.
- [ ] Ensure fixtures use synthetic identities and `.test` email domains.
- [ ] Run `python3 scripts/validate_skills.py`.
- [ ] Run `gitleaks git .` and investigate all findings.
- [ ] Run `git status --short`, `git diff --cached --stat`, and inspect full staged diff.
- [ ] Enable GitHub repository security features available to the account, including Dependabot alerts and public-repo secret scanning; enable push protection if available.
- [ ] Do not publish `.env` values or credentials in issues, discussions, screenshots, or CI logs.

If a credential was ever committed, rotate/revoke it before publishing. Deleting the file in a later commit does not remove it from Git history.
