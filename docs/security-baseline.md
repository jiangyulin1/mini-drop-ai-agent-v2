# Release security baseline

## Repository and credentials

- Real `.env` files, certificates and private keys are ignored and must not be committed.
- CI runs `scripts/check_repo_hygiene.py` against every tracked file.
- Deployment documentation uses placeholders; credentials belong in the environment or an
  external secret manager and must be rotated after any accidental disclosure.

## Web dependency exception

The production dependency gate fails on high and critical advisories, with one explicit
exception: `GHSA-qwww-vcr4-c8h2` in React Router.

Mini-Drop uses React Router only as a client-side `BrowserRouter`. It does not enable React
Server Components, data-router server actions, or the request handler affected by this
advisory. The exception is encoded in `web/scripts/check-audit.mjs` so that it is narrow,
reviewable and cannot hide unrelated advisories. Remove the exception as soon as an upstream
release resolves the advisory without introducing a conflicting client-side advisory.
