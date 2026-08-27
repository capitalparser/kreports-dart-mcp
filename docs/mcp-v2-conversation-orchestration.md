# MCP 2026 conversation orchestration

This document describes the optional MCP SDK v2 sidecar for KReports. The
existing MCP 1.x server remains the default runtime until the sidecar has been
validated against the internal web chatbot.

## Goals

The sidecar addresses four product requirements without changing the accounting
or financial-analysis semantics:

1. Ask the user for material comparison choices through a structured form.
2. Keep business-task state outside the model context window.
3. Return company lists in five-company pages without recomputing the analysis.
4. Reduce latency through deterministic cache keys, single-flight execution,
   worker-thread isolation, and bounded concurrency for heavy tools.

## Runtime separation

The repository intentionally maintains two isolated SDK environments during the
migration.

### Existing server

```bash
python -m pip install -e ".[dev,api]"
kreports-mcp
```

The project dependency remains `mcp>=1.0,<2.0` because the existing server uses
the MCP 1.x low-level API.

### Optional MCP 2026 sidecar

```bash
python -m venv .venv-mcp-v2
. .venv-mcp-v2/bin/activate
python -m pip install -e . --no-deps
python -m pip install -r requirements-mcp-v2.txt
kreports-mcp-v2 --transport stdio
```

HTTP example:

```bash
export KREPORTS_MCP_TOKEN='...'
export KREPORTS_STATE_SIGNING_KEY='at-least-32-bytes...'
export KREPORTS_MCP_REQUEST_STATE_KEY='at-least-32-bytes...'
kreports-mcp-v2 \
  --transport http \
  --host 127.0.0.1 \
  --port 8766 \
  --path /mcp
```

The v2 entry point fails early when imported with MCP SDK 1.x. Conversely, the
default environment does not install SDK 2.x, avoiding dependency ambiguity.

## Host metadata contract

The trusted web-chatbot backend supplies application-only request metadata. The
model must not be allowed to invent or override the authenticated identity.

```json
{
  "io.kreports/context": {
    "userId": "authenticated-user-id",
    "conversationId": "chat-thread-id",
    "clientId": "internal-web-chatbot",
    "interactive": true,
    "savePreferences": false,
    "stateHandle": "opaque-state-handle-from-prior-result",
    "pageToken": "opaque-page-token-from-prior-result",
    "recentTurns": [
      {"role": "user", "content": "삼성전자 동종기업을 보여줘"},
      {"role": "assistant", "content": "첫 5개 회사를 표시했습니다."}
    ]
  }
}
```

Rules:

- `userId`, `conversationId`, and `clientId` come from the trusted host.
- `interactive=true` permits the server to ask for a form when material
  comparison criteria are missing.
- `savePreferences=true` persists the accepted comparison preference inside the
  current conversation state; it is not a global user preference service.
- `stateHandle` and `pageToken` are opaque and identity-bound.
- `recentTurns` is capped at eight short turns; full chat history is not sent to
  KReports.

## Poll and multi-round-trip behavior

When a peer-analysis request is materially ambiguous, the sidecar returns a
form request instead of guessing silently. The form asks for:

- 연결 / 별도 / 확보된 자료 우선
- 세부업종 / 넓은 산업군
- 매출 / 총자산 / 규모 제한 없음
- 엄격 적용 / 부족 시 확대 / 유사도 순위
- 주석자료 보유 여부

For a 2026-07-28 client this is carried as an `InputRequiredResult`. The client
renders the JSON Schema as chips, radio buttons, or a modal and automatically
retries the original tool call with the accepted response and sealed
`requestState`.

The server asks only when all of the following are true:

1. the host marked the request interactive;
2. the user did not already supply explicit peer criteria; and
3. the conversation has no saved peer preference.

For older clients that cannot complete the modern form round trip, the current
migration adapter returns bounded Korean text choices. The internal web chatbot
should implement the structured form path.

## Explicit state and context-window management

Conversation state is not model memory. The server stores a small structured
record containing:

- current task and subject company;
- applied comparison criteria;
- result references;
- current page;
- optional conversation-scoped preferences; and
- paused tasks in the same chat.

The model receives a compact `kreports.context.v1` snapshot containing the
active-task summary, up to eight recent turns, and result references. The
following payloads remain outside the context window:

- complete company lists;
- full multi-year metric rows;
- raw note text;
- raw filing documents; and
- complete exclusion lists.

This prevents a long chat from repeatedly paying the token cost of prior
results and reduces the risk that old criteria are mixed with the active task.

## State-handle security

State and page tokens are HMAC-signed and bound to:

- authenticated user;
- conversation;
- client application; and
- expiry time.

A token from another user or conversation fails closed. Tokens contain no DART
API key, filing body, or private database path.

The default in-memory store is process-local. Production multi-worker HTTP
requires a shared store such as Redis and stable shared secrets:

- `KREPORTS_STATE_SIGNING_KEY`
- `KREPORTS_MCP_REQUEST_STATE_KEY`

Deploying multiple workers with process-local keys would invalidate Poll state
and page tokens when the next request lands on another worker.

## Five-company result pages

The first analytical call computes the full eligible population for statistical
accuracy but stores the company rows server-side. The response exposes only the
first five companies plus a page token.

```json
{
  "offset": 0,
  "pageSize": 5,
  "returned": 5,
  "total": 42,
  "nextPageToken": "opaque-token"
}
```

The next-page request reads rows 6-10 from the stored result. It does not:

- rerun peer selection;
- recompute percentiles;
- rerun note extraction; or
- send the first 40 companies in the initial response.

The web chatbot may route Previous/Next button actions directly to MCP without
calling the language model.

## Latency controls

The v2 sidecar adds a bounded execution coordinator:

- cache key = prepared dataset identity + tool + normalized arguments;
- five-minute default TTL;
- single-flight de-duplication for identical concurrent requests;
- worker-thread isolation for existing synchronous handlers;
- configurable semaphore for heavy tools; and
- no cache for `fetch_disclosure_on_demand`.

Environment variables:

```text
KREPORTS_TOOL_CACHE_TTL_SECONDS=300
KREPORTS_HEAVY_TOOL_CONCURRENCY=4
KREPORTS_CONVERSATION_TTL_SECONDS=86400
KREPORTS_RESULT_TTL_SECONDS=3600
```

The response `_meta` includes application-only performance evidence:

```json
{
  "io.kreports/performance": {
    "cacheHit": false,
    "sharedExecution": true,
    "durationMs": 183.4
  }
}
```

These fields belong in telemetry or an administrator view, not in the user
answer.

## Native MCP features used

The sidecar adopts the following MCP 2026 capabilities:

- `server/discover` and stateless Streamable HTTP;
- native `InputRequiredResult` multi-round-trip forms;
- sealed `requestState` boundary;
- tool `outputSchema` and validated `structuredContent`;
- application-only result `_meta`;
- extension capability `io.kreports/conversation`;
- `ttlMs` and `cacheScope` hints on lists and resources; and
- standard MCP HTTP method/name/version headers supplied by the SDK.

The DART links remain ordinary user-facing links and source records. A future
MCP Apps adapter may render the same interaction and page contracts inside a
portable embedded UI, but the internal web chatbot does not depend on MCP Apps.

## Capability and fallback policy

The host should register an elicitation callback only for interactive user
sessions. Background jobs, configuration checks, and non-interactive REST calls
must omit that capability and either provide explicit criteria or accept the
server defaults.

No destructive or sensitive value is collected through this form. API keys,
credentials, OAuth, payment, or other sensitive input must use an out-of-band
URL flow or the platform secret store.

## Recalculation rules

Changes to any of the following invalidate the peer population and dependent
analysis:

- subject company;
- business year;
- 연결/별도 basis;
- industry scope;
- size basis;
- include/exclude companies; or
- peer criteria.

The following actions reuse the stored result:

- next or previous five companies;
- switching table/chart presentation;
- opening a DART link; and
- expanding a previously returned excerpt.

A note-only follow-up reuses the existing peer population and queries only the
requested note topic.

## Validation

Run deterministic state/runtime tests in the default environment:

```bash
pytest -q \
  tests/test_conversation_orchestration.py \
  tests/test_mcp_v2_runtime.py
```

Then create the isolated v2 environment and run:

```bash
pytest -q \
  tests/test_mcp_v2_native.py
```

Finally rerun the PR #4 chatbot and analysis contracts in their original MCP
1.x environment. Record exact commands, Python/MCP versions, and pass/fail
counts in the Draft PR. GitHub Actions remain optional and manual-only.
