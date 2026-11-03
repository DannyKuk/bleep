# GitHub fixtures

Recorded from the GitHub REST API (`api.github.com`, version 2022-11-28) on 2026-11-03, on the public repo `encode/httpx`, and trimmed to the fields Bleep reads. Commit authors' email addresses were dropped.

| File | Request |
|---|---|
| `commits.json` | `GET /repos/encode/httpx/commits?per_page=3&path=httpx/_client.py` |
| `commit-small.json` | `GET /repos/encode/httpx/commits/15e21e9ea3cad4f06e22a7e704aabefdf43d2e29` (1 file) |
| `commit-large.json` | `GET /repos/encode/httpx/commits/80960fa31918d7663c3f4c3ad61661cf0e80628f` (6 files, 14,409 characters of patches) |
| `commit-not-found.json` | `GET /repos/encode/httpx/commits/0000000`, answered 422 |
| `not-found.json` | `GET /repos/encode/no-such-repo-bleep/commits`, answered 404 |
| `readme.json` | `GET /repos/encode/httpx/readme` |
| `contents-docs.json`, `contents-docs-advanced.json` | `GET /repos/encode/httpx/contents/docs`, `.../contents/docs/advanced` |
| `contents-docs-http2.json` | `GET /repos/encode/httpx/contents/docs/http2.md` |

`rate-limited.json` is not recorded: triggering the limit would have spent this machine's unauthenticated hour. It is the body GitHub documents for a spent primary rate limit, with a documentation address for the IP; the tests add the `x-ratelimit-*` or `retry-after` headers that GitHub sends with it.
