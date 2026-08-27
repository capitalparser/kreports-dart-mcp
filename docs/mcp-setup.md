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

Claude Web cannot start a local stdio launcher. Use the production remote MCP
endpoint instead:

```text
https://mcp.dartmcp.com/mcp
```

This is a public, read-only, no-auth endpoint. Enter the URL in the client's
remote MCP/Streamable HTTP setup and add **no headers, API key, bearer token, or
OAuth client**. The service holds no DART key and cannot collect or write data.

For ChatGPT web, enable Developer mode if it is available to your plan or
workspace, create an app under **Settings or Workspace settings → Apps →
Create**, enter the same URL, select no authentication if prompted, then scan
the tools. Current plan availability is documented in the
[official ChatGPT Developer mode guide](https://help.openai.com/en/articles/12584461-developer-mode-and-full-mcp-connectors-in-chatgpt).

`/healthz` is public liveness. `/readyz` is not externally routed; it is the
container-only release gate. A direct MCP connection uses each user's own
Claude, ChatGPT, Codex, Cursor, or other client account, so it does not require
the KReports operator to make an OpenAI API call.

For a private local experiment, keep `--allow-unauthenticated` on `127.0.0.1`,
`::1`, or `localhost` only. It rejects wildcard and external bind addresses.
The explicit `--public` flag is reserved for the reviewed Lightsail deployment
in `deploy/lightsail/`, which adds TLS, a path-only proxy, immutable data mounts,
and request-size limits.

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
