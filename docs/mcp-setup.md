# KReports MCP setup for IDEs and CLI

KReports exposes a local stdio MCP server. It can be used from VS Code Copilot,
Cursor, Claude Desktop, Claude Code, or any MCP client that can start a local
process.

## Prerequisites

1. Create `kreports-dart/.env`.

   ```env
   DART_API_KEY=your_opendart_api_key
   ```

2. Confirm the CLI environment.

   ```powershell
   cd "C:\Users\kkim44\Desktop\FY25\AI\Vive coding\LLM_Wiki\kreports-dart"
   python -m kreports.cli.main mcp-doctor
   ```

3. Start the raw MCP server if you want a terminal smoke check.

   ```powershell
   .\scripts\kreports-mcp.cmd
   ```

   The command waits for MCP JSON-RPC on stdin. Stop it with `Ctrl+C`.

## VS Code workspace

VS Code uses `.vscode/mcp.json` with a `servers` object for workspace-level MCP
servers. This workspace has already been configured at:

```text
C:\Users\kkim44\Desktop\FY25\AI\Vive coding\LLM_Wiki\.vscode\mcp.json
```

The KReports entry is:

```json
{
  "servers": {
    "kreports": {
      "type": "stdio",
      "command": "C:\\Users\\kkim44\\Desktop\\FY25\\AI\\Vive coding\\LLM_Wiki\\kreports-dart\\scripts\\kreports-mcp.cmd",
      "envFile": "C:\\Users\\kkim44\\Desktop\\FY25\\AI\\Vive coding\\LLM_Wiki\\kreports-dart\\.env"
    }
  }
}
```

In VS Code, run `MCP: List Servers` or open `.vscode/mcp.json` and start the
`kreports` server from the editor action.

To install it into the VS Code user profile instead of the workspace:

```powershell
cd "C:\Users\kkim44\Desktop\FY25\AI\Vive coding\LLM_Wiki\kreports-dart"
python -m kreports.cli.main mcp-config --target code-cli
```

Copy and run the printed `code --add-mcp ...` command.

## Cursor / Claude Desktop

Use this shape in the relevant MCP config file:

```json
{
  "mcpServers": {
    "kreports": {
      "command": "C:\\Users\\kkim44\\Desktop\\FY25\\AI\\Vive coding\\LLM_Wiki\\kreports-dart\\scripts\\kreports-mcp.cmd"
    }
  }
}
```

The launcher loads `kreports-dart/.env`, so the API key does not need to be
duplicated in every client config.

## Claude Web / remote MCP

Claude Web cannot start the local stdio launcher. It must connect to a public
HTTP remote MCP endpoint. KReports now exposes the same tools through
Streamable HTTP:

```powershell
cd "C:\Users\kkim44\Desktop\FY25\AI\Vive coding\KJ_Wiki\kreports-dart"
$env:KREPORTS_MCP_TOKEN="replace-with-long-random-token"
python -m kreports.cli.main serve-http --host 127.0.0.1 --port 8765 --path /mcp
```

Health check:

```powershell
Invoke-RestMethod http://127.0.0.1:8765/healthz
```

For Claude Web, expose the local server through HTTPS, for example with a
Cloudflare Tunnel:

```powershell
cloudflared tunnel --url http://127.0.0.1:8765
```

Then add the connector URL in Claude Web as:

```text
https://<your-tunnel-host>/mcp
```

Security notes:

- Remote MCP traffic originates from Anthropic's cloud, not your browser.
- Claude Web supports authless and OAuth remote MCP servers. This KReports
  implementation currently provides static bearer-token protection for MCP
  clients that can send an `Authorization: Bearer ...` header.
- If you test Claude Web authless, start the server with
  `--allow-unauthenticated` and use a short-lived tunnel only. Do not expose a
  long-lived unauthenticated endpoint with audit/client data.
- For production Claude Web use, put this behind an OAuth-capable gateway or a
  deployment layer that restricts access appropriately.

## Useful CLI commands

```powershell
python -m kreports.cli.main mcp-doctor
python -m kreports.cli.main mcp-doctor --json
python -m kreports.cli.main mcp-config --target vscode
python -m kreports.cli.main mcp-config --target cursor
python -m kreports.cli.main mcp-config --target claude
python -m kreports.cli.main serve-http --host 127.0.0.1 --port 8765
```

Available MCP tools include company search, financial snapshot, going-concern
score, restatement detection, accounting policy extraction, auditor history,
subsidiary auditor matrix, industry comparison, and business report overview.
Company inputs should be an exact or unique company name, 6-digit stock code, or
8-digit DART corp_code. Ambiguous names return candidate companies. Successful
company-level responses include `_meta.data_freshness` so clients can show when
the local DART cache was last collected.
