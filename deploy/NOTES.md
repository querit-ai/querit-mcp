# Deployment notes

## Required production decisions

Before enabling anonymous access, provision:

- a dedicated Querit `mcp-anonymous` API credential with an explicit upstream budget;
- Redis/Valkey reachable from all MCP replicas;
- a high-entropy HMAC secret for pseudonymous IP quota keys;
- trusted proxy CIDRs and an edge rule that overwrites the forwarded client IP header.

Do not use a normal user's free API Key as the shared credential.

## Secrets

Inject these values from a Kubernetes Secret or an external secret manager:

- `QUERIT_MCP_FREE_API_KEY`
- `REDIS_URL`
- `MCP_FREE_IP_HMAC_SECRET`

They must not be stored in a ConfigMap, image layer, log message, metric label, or trace attribute.

## Non-secret configuration

```text
MCP_FREE_TIER_ENABLED=true
REDIS_TIMEOUT_SECONDS=1
MCP_FREE_QPS_PER_IP=2
MCP_FREE_DAILY_CREDITS_PER_IP=50
MCP_FREE_SEARCH_MAX_COUNT=5
MCP_FREE_CONTENTS_MAX_URLS=3
MCP_FREE_CONTENTS_MAX_CHARS=4000
MCP_FREE_CRAWL_TIMEOUT_SECONDS=15
MCP_ALLOWED_HOSTS=mcp.querit.ai
TRUSTED_PROXY_CIDRS=<ingress-or-load-balancer-cidrs>
UPSTREAM_TIMEOUT_SECONDS=75
HOST=0.0.0.0
PORT=8000
```

`MCP_FREE_TIER_ENABLED` defaults to `false`. Production must opt in explicitly after all required
secrets are present. `REDIS_URL` remains required configuration, but runtime Redis failures are logged
and fail open for anonymous calls. A missing trusted client IP still rejects anonymous calls. BYOK is
independent of Redis and the anonymous quota.

## Networking and timeouts

The public route is `/mcp`. Preserve POST, GET, DELETE, and OPTIONS methods supported by the MCP transport. Configure timeouts in this order:

```text
contents crawl_timeout <= 60s
  -> MCP upstream timeout 75s
    -> load balancer / ingress timeout >= 90s
      -> MCP client tool timeout >= 90s
```

Expose `/healthz` for liveness and `/readyz` for readiness. These endpoints intentionally do not call Querit or Redis.

## Logging and Google Cloud

The server writes single-line JSON records to stdout. GKE parses these records into `jsonPayload`,
uses the explicit `severity`, and recognizes access-log fields under `httpRequest`. Exception details
remain escaped inside one JSON record, so one traceback produces one Cloud Logging entry instead of one
entry per traceback line.

Use `jsonPayload.event` as the stable event key. The main values are:

| Event | Meaning |
| --- | --- |
| `tool_call_completed` | A search or contents tool call completed successfully. |
| `tool_call_rejected` | Authentication, validation, or anonymous quota policy rejected a call. |
| `tool_call_failed` | A tool call failed because of an upstream or unexpected operational error. |
| `upstream_request_failed` | The Querit API timed out, failed at the network layer, or returned an error. |
| `redis_rate_limit_fail_open` | Anonymous quota enforcement failed open; repeated failures are aggregated. |
| `redis_rate_limit_recovered` | Redis quota enforcement recovered after a failure period. |
| `http_request_completed` | A non-probe HTTP request completed. |

Example Cloud Logging query:

```text
resource.type="k8s_container"
resource.labels.container_name="querit-mcp"
jsonPayload.event="redis_rate_limit_fail_open"
severity>=ERROR
```

Successful `httpx` requests, MCP session lifecycle messages, and `/healthz` or `/readyz` access logs are
suppressed at the default `LOG_LEVEL=INFO`. Logs intentionally omit query text, requested URLs, response
bodies, raw client IP addresses, authorization headers, API Keys, and Redis URLs.

## Rollout checks

1. Install the remote MCP without a Key and complete search -> contents.
2. Exhaust a staging anonymous quota and verify a tool-level limit error.
3. Configure a user Key and verify BYOK bypasses the anonymous quota.
4. Configure an invalid user Key and verify it does not fall back to the shared credential.
5. Block Redis and verify anonymous calls continue, one `redis_rate_limit_fail_open` event is logged,
   repeated failures are aggregated, and BYOK continues.
6. Rotate the shared credential and HMAC secret without rebuilding the image.
