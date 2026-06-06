# Security Policy

## Supported Versions

ESFEX is released from the `main` branch and published to
[PyPI](https://pypi.org/project/esfex/). Security fixes are applied to the
latest released `0.1.x` version. Always run the most recent release.

| Version          | Supported          |
| ---------------- | ------------------ |
| Latest `0.1.x`   | :white_check_mark: |
| Older `0.1.x`    | :x:                |

## Reporting a Vulnerability

Please **do not** report security vulnerabilities through public GitHub issues,
pull requests, or discussions.

Report them privately instead, by either:

- **GitHub Security Advisories** (preferred) — use the repository's
  [**Security → Report a vulnerability**](https://github.com/Net-Zero-Horizon/ESFEX/security/advisories/new)
  form, which is private to the maintainers and tracks the report to resolution.
- **Email** — alternatively, send a description to **manuel.sotocalvo@gmail.com**
  with the subject line `ESFEX security`.

Please include:

- A description of the vulnerability and its impact.
- Steps to reproduce — a minimal proof of concept, the affected version, and
  your environment (OS, Python and Julia versions).
- Any suggested remediation, if you have one.

## What to Expect

- **Acknowledgement** within **5 business days**.
- An initial assessment and severity triage within **10 business days**.
- We will keep you informed of progress and agree a disclosure timeline with
  you. For confirmed high-severity issues we aim to release a fix as promptly
  as practical.
- With your consent, we will credit you in the release notes or advisory once a
  fix is published.

## Scope

**In scope**

- The `esfex` Python package and the bundled Julia optimization code in this
  repository.

**Out of scope**

- Vulnerabilities in third-party dependencies — please report those upstream;
  we will bump our pinned versions once an upstream fix is available.
- Issues that require an attacker who already has full local control of the
  machine, or that depend on running untrusted model/configuration files. ESFEX
  executes user-provided configurations (including embedded Julia/solver code)
  by design, so only run configurations you trust.

## Disclosure Policy

We follow coordinated disclosure: please give us a reasonable opportunity to
release a fix before any public disclosure, and we will work with you to agree
an appropriate timeline.
