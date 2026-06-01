# Security Policy

## Supported versions

KReports is currently pre-1.0. Security fixes are applied to the main branch and
the latest published package when applicable.

## Reporting a vulnerability

Please report security issues privately through GitHub Security Advisories for
this repository, or contact the maintainer through the GitHub profile if
advisories are unavailable.

Do not open a public issue for vulnerabilities involving:

- API key exposure.
- Remote MCP authentication bypass.
- Unauthorized access to private runtime databases.
- Unsafe handling of raw DART filings or customer/client data.
- Code execution through parser inputs, MCP arguments, or dashboard uploads.

## Security model

KReports separates public read paths from private collection paths.

- Public MCP servers are intended to be read-only.
- Maintainer collectors use private DART API keys outside the repository.
- Remote HTTP MCP should use bearer tokens, OAuth-capable gateways, or another
  deployment layer that restricts access.
- Raw filings and runtime databases can contain large public disclosure content,
  but they should still be treated as operational data and excluded from Git.
- User-supplied on-demand DART API keys should be used only for the requested
  call path and must not be persisted by default.

## Secret handling

Never commit:

- `DART_API_KEY`
- OpenAI or Anthropic API keys
- GCS credentials
- SQLite runtime databases
- raw DART document archives
- customer/client names or audit workpapers

Use `~/.config/kreports/collector.env` or the deployment platform's secret
manager for collector secrets.
