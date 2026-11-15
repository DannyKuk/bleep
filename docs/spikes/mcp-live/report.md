# Spike: the MCP plugin on Coolify's own MCP server

Measured on 2026-11-15 (#371). Step 2 ran with [`spike.py`](spike.py) inside the live backend container and called no model. Step 3 was one live incident on the running Bleep with a real model, on the permanent demo app ([demo](../../demo.md)).

**Every number below comes from the saved runs of the script (10:52 to 10:58 UTC), the incident's records (events and usage rows) or OpenRouter's key endpoint.** Latencies are single calls, not a spread.

> **Answer:** keep it, marked experimental (#591). The plugin works on a real MCP server: it connected, listed Coolify's 60 tools, ran every allowed read tool in about 0.6 s, kept every answer under the guard's cap with no token echo, and refused a write tool before the server saw it. In a live incident the model used it (`mcp_tools`, then `get_application` and `list_unhealthy_resources`) but got nothing the Coolify plugin's own read tools don't give: the plan was the same `docker.restart` as the run without MCP the day before, for about the same cost ($0.0051 against $0.0056), and this time the model read no container logs. On Coolify the Coolify plugin is the better tool; the MCP plugin stays as the way to reach servers Bleep has no plugin for, labelled as experimental because it is proven only against this one server. Two bugs found: a long listing leaves allowed tools out (#592), and redaction hides the rest of a JSON line after a secret-looking key whose value is `null` (#593).

## Setup

| | |
|---|---|
| Bleep | `main` at `3b7ce88`, the images built from it, Docker Desktop on an M-series Mac |
| Coolify | v4.7.5 at `https://coolify.dkwebsite.de` (v4.6.0 in the [Coolify spike](../coolify-live/report.md)); MCP server already on under Settings > Advanced > API and MCP |
| MCP endpoint | `MCP_URL=https://coolify.dkwebsite.de/mcp`, set on the dashboard, so the plugin sends through the public-only transport |
| Token | `MCP_TOKEN`: a new Coolify API token with only the `read` permission (not `read:sensitive`), 30 days, all resources of the team, pasted on the dashboard from Coolify's copy button; the Coolify plugin kept its own `COOLIFY_TOKEN` |
| Plugin settings | purpose "Coolify's own MCP server: apps and their status and health, deployments, servers and their metrics"; read tools `search_resources`, `get_application`, `list_unhealthy_resources`, `get_server_metrics`, `get_infrastructure_overview`; no act tools |
| App | `bleep-demo-app` (uuid `bbn4xailqgdqggfj4pyl7wzl`), broken with its Error state button |
| Model | `openai/gpt-6-luna` through OpenRouter |

## Step 1: the plugin on

- With no token, a POST to `/mcp` with the MCP `Accept` header answered 401, so the endpoint was on before the spike.
- Switched on from the Settings page; the health check said "the server lists 60 tools; Bleep may run 5 as read and 0 as act". Listing the tools takes 0.44-0.53 s (one session: initialize, list).
- Every tool carries annotations: 46 say `readOnlyHint: true`, 14 (`manage_dns`, `manage_proxy`, `docker_cleanup`, `cloud_provider` and the other `manage_*`) say `readOnlyHint: false` and most `destructiveHint: true`. A read token still gets all 60 listed; Coolify refuses the write ones at call time, and Bleep would refuse them as read tools anyway (`marked`).

## Step 2: `mcp_tools` and `mcp_call` on the real server

| Call | Latency | Raw answer | Model's output | Notes |
|---|---|---|---|---|
| `mcp_tools` | 438-490 ms | 60 tools | 6,927-7,055 chars | 17-18 tools shown, "42/43 more tools not shown"; of 8 allowed read tools only 4 were in it (#592) |
| `get_infrastructure_overview` | 692 ms | 752 chars | 826 | version, server, projects with counts |
| `list_unhealthy_resources` | 603 ms | 168 chars | 242 | nothing unhealthy (the app was healthy then) |
| `get_application` (uuid) | 621 ms | 3,004 chars | 2,411 | 1 redaction that hid about 600 harmless chars after `"http_basic_auth_username":null` (#593) |
| `list_deployments` | 616 ms | 76 chars | 150 | empty: it lists running deployments only |
| `list_env_keys` (application) | 558 ms | 826 chars | 900 | keys and flags, no values |
| `search_resources` ("bleep-demo") | 609 ms | 343 chars | 417 | the app with `running:healthy` and its domains |
| `get_server_metrics` | 604 ms (2,986 ms the first call) | 2,277 chars | 2,351 | CPU, memory, disk, load over 60 min |
| `get_server_resources` | 615 ms | 868 chars | 942 | every resource on the server |
| `coolify_help` | 598 ms | 7,700 chars | 3,584 | the redaction of #593 again, after `"manage_integration_tokens"` |
| `manage_proxy` (not allowed) | 1 ms | not called | 116 | refused by Bleep: "not one of the MCP plugin's read tools" |

- **Every output was within the 8,000-char cap and wrapped as `<untrusted source="mcp">`.** The token appeared in no raw answer and no output; no credential-shaped value (`password`, `secret`, `token`, `api_key` with a value of 6+ characters) was in any raw answer.
- **The listing is the weak point.** Coolify's 60 tools need about three times the 7,000 characters `mcp_tools` keeps, and the listing goes in the server's order, so allowed tools late in that order are dropped while tools the model may not run take the space (#592). Each tool's own entry is well formed: name and label, description, schema and `required`.
- **Redaction hid useful fields.** A secret-looking key with an unquoted value hides the rest of the line in compact JSON (`{"password":null,"a":1}` becomes `{"password":<redacted>`), which cut `restart_count`, `container_present` and `health_check_type` out of `get_application` (#593). Nothing secret leaked; the loss is on the safe side.

## Step 3: a live incident

The Error state button made `/health` answer 500. Bleep opened `inc_01M7FDXARS5PHGVT6Z` ("url failed on coolify/bbn4xailqgdqggfj4pyl7wzl") at 11:01:37 and asked for approval at 11:02:08 (31 s).

| | With MCP (2026-11-15) | Without MCP, same fault (`inc_01M7DW6VBF7VPMVDBK`, 2026-11-14) |
|---|---|---|
| Investigate's tools | `list_containers`, `mcp_tools`, `inspect_container`, `recent_events`, `mcp_call get_application`, `mcp_call list_unhealthy_resources`, `deployed_commits`, `commit_diff` | `list_containers`, `inspect_container`, `container_logs`, `recent_events`, `coolify_applications`, `deployed_commits`, `coolify_deployments`, `commit_diff` |
| Plan | `docker.restart` of the app container | `docker.restart` of the app container |
| Reason given | the Error fault exhausts the cache pool and clears on restart (commit diff, incident history) | the same |
| Model calls | 6, 65,679 tokens in | 7, 68,150 tokens in |
| Cost | $0.0051 | $0.0056 |

- **The model used the plugin because the purpose told it to**: `mcp_tools` second, then two of the five read tools, each answered in about 1 s. `list_unhealthy_resources` asked for a sample (`sample_only`, `sample_per_type`), arguments it read from the listing's schema.
- **It added nothing.** The diagnosis and plan came from the commit diff and the incident history, as the day before. `list_unhealthy_resources` answered that nothing was unhealthy while the container was unhealthy (Coolify had not noticed yet), which could have misled a weaker run.
- **It displaced better reads.** With MCP the model called no `container_logs` and none of the Coolify plugin's own tools; the 7 KB listing took the room. One run, so this is a sign, not a measurement.
- After the person's yes (11:09:08) the restart ran, Verify found the container healthy and the URL answering on its first attempt, and the incident closed fixed and verified at 11:09:32; the report added one more model call, $0.0052 in all.
- OpenRouter's usage went from $8.66013 before to $8.66162 right after Propose ($0.0015; its counter lags), the usage rows say $0.0051.

## Decision

**Keep the MCP plugin, marked experimental on the dashboard with a beaker icon and a line that it may not work with every MCP server (#591).** Danny's call (2026-11-15): it works, so there is no reason to delete it, and as an experimental feature it offers other MCP servers too. What the spike shows backs the label: on Coolify the Coolify plugin covers everything the MCP read tools gave in an incident, with typed actions and tests, so a person with Coolify should use that; the MCP plugin is for servers Bleep has no plugin for, and it is proven against one server only.

Follow-ups: #591 (experimental label), #592 (allowed tools first in the listing), #593 (redaction of a JSON line after a `null` value).

## Nothing changed on Coolify

The MCP token has only `read`; the only write call (`manage_proxy`) was refused by Bleep before it was sent. The only change on Coolify was its token list: the read token was made for the spike and revoked after it. On Bleep, the MCP plugin was switched off again after the spike (its settings and `MCP_URL` kept). The incident's restart was a Bleep `docker.restart` over SSH after the person's yes, not an MCP call.

## Limits

- One server (Coolify 4.7.5), one fault, one incident: the comparison with the day before is two runs, not a spread.
- No act tool was tried live; `mcp.call` is still proven only against the eval's fixture server (`misroute-mcp`).
- Coolify's server sends plain JSON answers (no SSE event stream, no compression), so the 2 MB cap and the compressed-answer refusal were not exercised.

## Reproduce

```
# after MCP_URL and MCP_TOKEN are set on the dashboard
docker exec -i -e SPIKE_READ=get_application,search_resources \
  -e SPIKE_CALLS='[["get_application",{"uuid":"<app uuid>"}],["search_resources",{"query":"<name>"}]]' \
  bleep-backend-1 /app/.venv/bin/python - < docs/spikes/mcp-live/spike.py
```

Then switch the plugin on with a purpose and read tools, break an app on a watched host, and read the incident's `tool_call` events and usage rows.
