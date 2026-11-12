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
| `compare.json` | `GET /repos/encode/httpx/compare/80960fa31918d7663c3f4c3ad61661cf0e80628f...4189b7f051c6c51ce74c3bee1a5f269f9c50c6b2` (10 commits, oldest first as GitHub lists them); recorded on 2026-11-12 and trimmed to `status`, `ahead_by`, `behind_by`, `total_commits` and the commits' fields above, without the `files`, `base_commit` and `merge_base_commit` Bleep ignores (#351) |
| `unauthorized.json` | `GET /repos/DannyKuk/bleep/commits` with a made-up token, answered 401 with no `x-ratelimit-*` headers; recorded on 2026-11-09 in the [GitHub live spike](../../../../docs/spikes/github-live/report.md) |
| `compare-express-*.json` | `GET /repos/expressjs/express/compare/147c250...547fdd4` (the tags 4.0.0 and 4.18.0, 1,371 commits), unpaged and with `page`/`per_page` of 2/1, 13/99, 14/98, 1/71 and 15/100 (past the end, no commits); recorded on 2026-11-12 without a token and trimmed to `status`, `ahead_by`, `behind_by`, `total_commits`, each commit's `sha`, `commit.author`/`committer` (name and date), `commit.message`, `author.login` and `html_url`, and, on the unpaged answer and page 1, the first two of the real 270 `files` (name, status and line counts, no patches), so every field kept has GitHub's shape (#497) |

`rate-limited.json` is not recorded: triggering the limit would have spent this machine's unauthenticated hour. It is the body GitHub documents for a spent primary rate limit, with a documentation address for the IP; the tests add the `x-ratelimit-*` or `retry-after` headers that GitHub sends with it.

On 2026-11-09 the [GitHub live spike](../../../../docs/spikes/github-live/report.md) compared these shapes with real answers for a private repo read with a token: every field Bleep reads is there with the same type, and the real answers only add fields Bleep ignores (`node_id`, `parents`, `commit.tree`, `commit.verification`, emails, the files' `sha`, `blob_url`, `raw_url` and `contents_url`). One case the trimmed files do not show: GitHub sends no `patch` for an empty file, as for a binary one (#345).

A compare of over 250 commits is not recorded in full (a real one is megabytes of file patches). `tests.fakes.PagedCompare` builds one in `compare.json`'s commit shape and answers `page` and `per_page` as GitHub did on 2026-11-12 for a real public range of 1,371 commits (expressjs/express 4.0.0...4.18.0, probed unauthenticated): the commits oldest first, the newest 250 when asked without paging, a page past the end a 200 with no commits, `total_commits` on every page and the `files` list on the first only (#497).
