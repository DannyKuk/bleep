# Scenario evals, 2026-11-12

> `openai/gpt-6-luna` diagnosed and proposed for 1 demo fault, 6 runs each; `claude-code:opus` judged the root causes. The root cause passed in 5 of 6 runs and the plan was exact in 6 of 6.

## Setup

| | |
|---|---|
| Model under test | `openai/gpt-6-luna` (provider `openrouter`) |
| Judge | `claude-code:opus` through DeepEval GEval; a root cause passes at 0.7 or more |
| Scenarios | bad-deploy, 6 runs each |
| When | 2026-11-12 10:16 UTC, 69 min |
| Commit | `9950e3ef` |
| Raw data | [`scenarios-20261112-101647.jsonl`](data/scenarios-20261112-101647.jsonl) |
| Reproduce | `uv run --group evals python -m evals.run --only bad-deploy --repeat 3 --append docs/evals/data/scenarios-20261112-101647.jsonl` (in `backend/`) |

Each run switches the fault on, lets Bleep's watcher open the incident and runs the graph to the end of Propose, then switches the fault off. Nothing is executed. Times count from the fault going on; steps are the model's tool calls, and tools lists them in order with their arguments cut short (n/a for a run recorded before they were kept); peak prompt is the largest single prompt of the run; fallbacks are model calls that gave no structured output through tools, so Bleep asked for JSON in the reply instead. The shop's containers were recreated before every run, volumes kept, so no earlier run's log reached the next. Knowledge search was offered over the shipped Docker docs snapshot, as in the app, and the linked repo docs `DannyKuk/bleep: demo/shop/docs/deploy.md`, `DannyKuk/bleep: demo/shop/docs/services.md`, read from this checkout.

## bad-deploy

- Opened by the fault `bad-deploy`
- Expected root cause: shop-web version 2 reads the column price_cents, which the database does not have (its migration never ran), so /api/items fails with 500 while shop-web stays healthy.
- Expected category `code`, plan no action, never `docker.restart`, `docker.start`, `docker.stop`

| Run | Root cause | Category | Plan | Exact | Unnecessary | Steps | Tools | Tokens | Peak prompt | Cost | To incident | To diagnosis |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | 1.00 pass | code | no action | yes | no | 8 | `list_containers`, `recent_commits({"path": "demo", "since_hours": 720})`, `inspect_container({"container": "shop-web"})`, `inspect_container({"container": "shop-db"})`, `recent_events({"minutes": 60})`, `commit_diff({"max_chars": 8000, "sha": "d9f9be1"})`, `commit_diff({"max_chars": 8000, "sha": "3350063"})`, `container_logs({"container": "shop-web", "tail": 200})` | 41,509 | 8,853 | $0.0034 | 60 s | 81 s |
| 2 | n/a | code | no action | yes | no | 8 | `list_containers`, `recent_commits({"since_hours": 168})`, `commit_diff({"max_chars": 8000, "sha": "d9f9be1"})`, `commit_diff({"max_chars": 8000, "sha": "3350063"})`, `inspect_container({"container": "shop-web"})`, `container_logs({"container": "shop-web", "tail": 200})`, `recent_events({"minutes": 60})`, `inspect_container({"container": "shop-db"})` | 38,625 | 8,137 | $0.0028 | 60 s | 77 s |
| 3 | 0.90 pass | code | no action | yes | no | 8 | `list_containers`, `recent_commits({"path": "demo", "since_hours": 168})`, `recent_events({"minutes": 60})`, `inspect_container({"container": "shop-web"})`, `inspect_container({"container": "shop-db"})`, `commit_diff({"max_chars": 8000, "sha": "3350063"})`, `commit_diff({"max_chars": 8000, "sha": "d9f9be1"})`, `container_logs({"container": "shop-web", "since_minutes": 30, "tail": 120})` | 42,059 | 8,824 | $0.0034 | 60 s | 81 s |
| 4 | 0.90 pass | code | no action | yes | no | 8 | `list_containers`, `recent_commits({"path": "demo", "since_hours": 720})`, `inspect_container({"container": "shop-web"})`, `inspect_container({"container": "shop-db"})`, `recent_events({"minutes": 60})`, `commit_diff({"max_chars": 8000, "sha": "3350063"})`, `commit_diff({"max_chars": 8000, "sha": "d9f9be1"})`, `container_logs({"container": "shop-web", "tail": 200})` | 41,503 | 8,860 | $0.0034 | 60 s | 78 s |
| 5 | 0.90 pass | code | no action | yes | no | 8 | `list_containers`, `inspect_container({"container": "shop-web"})`, `inspect_container({"container": "shop-db"})`, `recent_commits({"since_hours": 720})`, `commit_diff({"sha": "d9f9be1"})`, `commit_diff({"sha": "3350063"})`, `recent_events({"minutes": 60})`, `container_logs({"container": "shop-web", "tail": 300})` | 41,019 | 8,804 | $0.0030 | 60 s | 76 s |
| 6 | 1.00 pass | code | no action | yes | no | 8 | `list_containers`, `inspect_container({"container": "shop-web"})`, `inspect_container({"container": "shop-db"})`, `recent_events({"minutes": 60})`, `recent_commits({"path": "demo", "since_hours": 720})`, `commit_diff({"max_chars": 8000, "sha": "d9f9be1"})`, `commit_diff({"max_chars": 8000, "sha": "3350063"})`, `container_logs({"container": "shop-web", "since_minutes": 120, "tail": 200})` | 40,861 | 8,789 | $0.0029 | 60 s | 76 s |

- Run 1 diagnosis: Commit d9f9be1 'Deploy shop 2' changed demo/compose.yaml to run shop-web with SHOP_VERSION 2, whose code from commit 3350063 'Keep item prices in whole cents for shop 2' queries price_cents, but the existing shop-db items table lacks that column because the 0002_price_cents.sql migration was not applied. This version/database mismatch causes /api/items to return 500.
  - Judge: The diagnosis gets the same root cause as the expected output. It names shop-web version 2 as the failing component and says why it fails: the code queries the price_cents column, but the shop-db items table doesn't have that column because the 0002_price_cents.sql migration never ran. As a result /api/items returns 500 while /health still passes. The diagnosis commits to this one cause and doesn't hedge between several. The extra detail is correct and helps: commit 3350063 added the migration and the price_cents read, and commit d9f9be1 changed the default SHOP_VERSION to 2. Labelling the category 'code' is reasonable, because the fault is a mismatch between the deployed code and the database schema. The diagnosis does not mistake the 500 symptom for the cause.
  - Commits named by sha: the diagnosis `d9f9be1`, `3350063`
- Run 2 diagnosis: SHOP_VERSION is set to 2 in demo/compose.yaml while shop-db lacks the price_cents column required by shop version 2; commit d9f9be1 'shop-dev: Deploy shop 2' changed the setting without the migration being applied to this database.
  - Judge: the judge failed: KeyError: 'score'
  - Commits named by sha: the diagnosis `d9f9be1`, `3350063`
- Run 3 diagnosis: Commit d9f9be1 'shop-dev: Deploy shop 2' changed SHOP_VERSION's default to 2, so shop-web queries price_cents, but shop-db's existing items table lacks that column because migration 0002_price_cents.sql was not applied. This version/schema mismatch causes /api/items to return 500.
  - Judge: The diagnosis gives the same root cause as the expected output. It says shop-web version 2 (deployed by commit d9f9be1 changing SHOP_VERSION to 2) queries price_cents, but shop-db's items table has no such column because migration 0002_price_cents.sql never ran. That is the same failing component and the same reason it fails. It treats the /api/items 500 as a symptom, not the cause. The extra evidence is correct and on point: the commit diffs, the missing-column errors in both shop-web and shop-db logs, and shop-db skipping initialization for an existing database directory. It commits to this one cause instead of listing several. Its only gap is that it does not say shop-web stays healthy, which is a minor detail and not part of the root cause.
  - Commits named by sha: the diagnosis `d9f9be1`, `3350063`
- Run 4 diagnosis: The shop-web v2 code queries `price_cents`, but the existing `shop-db` schema lacks that column because the v2 migration was not applied. Commit d9f9be1 'shop-dev: Deploy shop 2' set the `SHOP_VERSION` default to 2, making the code incompatible with the database schema.
  - Judge: The diagnosis names the same root cause as the expected output: shop-web v2 queries the price_cents column, which shop-db lacks because the v2 migration (0002_price_cents.sql) was never applied. It also supports this with the right evidence. The /api/items query fails on the missing column. Commit d9f9be1 switched SHOP_VERSION to 2. The shop-db log shows it found an existing database directory and skipped initialization, which explains why the migration never ran. All of this extra detail is correct. Minor gaps: it does not say that /api/items returns 500 while shop-web stays healthy, and the 'code' category is debatable for what is really a deploy-versus-schema mismatch. Neither changes the root cause, which is a full match.
  - Commits named by sha: the diagnosis `d9f9be1`, `3350063`
- Run 5 diagnosis: Commit d9f9be1 'shop-dev: Deploy shop 2' changed SHOP_VERSION in demo/compose.yaml from 1 to 2, so shop-web queries price_cents while the existing shop-db items table lacks that column because the migration adding it was not applied.
  - Judge: The diagnosis gives the same root cause as the expected output. shop-web version 2 queries the price_cents column, and the shop-db items table doesn't have it because the migration adding it was never applied. That is the same failing component and the same reason it fails. It also traces the change to commit d9f9be1, which bumped SHOP_VERSION from 1 to 2, and backs this up with log evidence that /api/items fails on the missing column while shop-db accepts connections. This extra detail is correct and consistent with the expected cause. It commits to a single cause and doesn't list alternatives. Minor gaps: it doesn't say the failure is a 500 or that shop-web itself stays healthy, but these are symptoms rather than the cause.
  - Commits named by sha: the diagnosis `d9f9be1`, `3350063`
- Run 6 diagnosis: The deployment switched shop-web to SHOP_VERSION=2, which queries price_cents, but the existing shop-db database lacks that column because the version 2 migration was not applied. The database mismatch causes /api/items to return 500.
  - Judge: The diagnosis gives the same root cause as the expected output. It says shop-web version 2 queries the price_cents column, and the shop-db database lacks that column because the version 2 migration (0002_price_cents.sql) was never applied. That is why /api/items returns 500 while /health still returns 200. The failing component and the reason for the failure both match. The diagnosis commits to this single cause instead of listing alternatives. Its supporting evidence is correct and specific: the shop-db logs show price_cents does not exist, commit d9f9be1 switched SHOP_VERSION to 2, and commit 3350063 added the migration. This extra detail is accurate and does not count against it.
  - Commits named by sha: the diagnosis `d9f9be1`, `3350063`

- The diagnosis named a commit of the repo by its sha in 6 of 6 diagnosed runs (#350).

## Totals

| Scenario | Runs | Root cause | Category | Exact plan | Unnecessary | Failed | Out of context | Fallbacks | Mean steps | Mean tokens | Max peak prompt | Cost | Mean to diagnosis |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| bad-deploy | 6 | 5 of 6 | 6 of 6 | 6 of 6 | 0 of 6 | 0 | 0 | 0 | 8.0 | 40,929 | 8,860 | $0.0188 | 78 s |
| **All** | 6 | 5 of 6 | 6 of 6 | 6 of 6 | 0 of 6 | 0 | 0 | 0 | 8.0 | 40,929 | 8,860 | $0.0188 | 78 s |

## Cost

- Model under test: $0.0188.
- Judge: n/a (billed to a plan, not per call).

## What it shows

- The root cause passed in 5 of 6 runs, and the category was right in 6 of 6.
- The plan was exactly the expected one in 6 of 6 runs; 0 proposed an unnecessary action.
- The largest single prompt was 8,860 tokens (bad-deploy, run 4).
- 0 structured-output fallbacks in 0 runs: model calls that gave no structured output through tools, so Bleep asked for JSON instead.
- The judge gave no score for 1 diagnosed run.
