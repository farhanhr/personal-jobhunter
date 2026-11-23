# Security Policy

## About this project

Personal Job Finder is a single-person, local-first application. It runs on your own
computer, listens only on `127.0.0.1`, and stores your data locally plus in the Google
Sheet you connect yourself. There is no hosted service, no shared backend, and no
account on our side.

That design removes most common risks, but it does not remove all of them. We still want
to hear about anything that could expose your data, your Google account, or your machine.

## Reporting a vulnerability

Please report privately through GitHub's **private vulnerability reporting** or
**Security Advisories** for this repository. If that is not available to you, open a
short public issue that asks for a private channel, and share details only after we
respond. Do not post the details themselves in a public issue.

## What is in scope

- The application's own code: the local backend, the local interface, and the command
  line tools.
- Handling of the Google Service Account key, local setup authorization, and spreadsheet
  access.
- Anything that could expose local data to another process, website, or person.
- Anything that could make the app act outside the limits it promises, such as
  exceeding the daily search limit, contacting sites it should not, or ignoring a
  site's request to slow down.
- Unsafe behavior in bundled dependencies that affects this app in practice.

## What is out of scope

- Problems in Google, LinkedIn, or company career sites themselves.
- A machine that is already compromised, or an attacker who can already read your user
  account files.
- Google Service Account key rotation performed in Google Cloud Console. Revoke or rotate
  the key there if it is ever exposed.
- Issues that require you to publish your own spreadsheet, tokens, or database.

## Please never include

- Google Service Account keys (the JSON file), private keys, or setup tokens.
- Your spreadsheet ID, real job data, CVs, or application documents.
- Recruiter names, email addresses, or other personal contact details.
- Screenshots or logs that contain any of the above.

If you are unsure whether something is sensitive, leave it out and describe it in words
instead.

## Supported versions

Please report against the latest commit on the default branch. This is a personal
project, so older versions are not separately maintained.

## What to expect

This project has no formal response-time commitment. Reports are reviewed on a
best-effort basis. If a report is valid, we will fix it, and if a credential may have
been exposed we will advise you to revoke and rotate it at the provider. Removing a
secret from the latest commit is not enough to remove it from history, so treat any
exposure as a rotation event first.
