# Connect a KReports MCP endpoint

The public repository does not contain the private collector or local database.
Connect to a hosted or internally deployed KReports MCP endpoint supplied by
your administrator.

## Claude Desktop, Cursor, or another MCP client

Use the URL form supported by your client:

```json
{
  "mcpServers": {
    "kreports": {
      "url": "https://your-kreports-host.example/mcp"
    }
  }
}
```

If the endpoint requires a bearer token, configure it through the client's
secret/environment mechanism. Never commit tokens or a `DART_API_KEY` here.

## Claude Web

Add `https://your-kreports-host.example/mcp` as a custom connector in Claude
Settings → Integrations. The endpoint must be HTTPS and protected according to
your organization's policy.

## Self-hosted deployments

Self-hosting requires authorized access to the private
[`capitalparser/kreports-core`](https://github.com/capitalparser/kreports-core)
repository, its deployment instructions, a release runtime artifact, and a
free [DART OpenAPI key](https://opendart.fss.or.kr) for collection. Keep the
collector and read-only MCP serving roles separate. The collector owns
credentials and writes data; the serving endpoint reads a verified runtime
artifact.

## What to verify before trusting an answer

Responses should include filing year, receipt/source locator, and an explicit
availability state. Treat `unavailable`, `unverified`, and `summary_only` as
limitations, not as confirmed original text. The selected release artifact is
the authority for company/year/topic coverage.
