# Lightsail public Remote MCP

This directory deploys the public, read-only KReports Remote MCP at:

```text
https://mcp.dartmcp.com/mcp
```

Users supply only that URL to a remote MCP client. There is deliberately no
KReports bearer token, OAuth login, DART key, or user billing path. Their chat
client uses their own ChatGPT, Claude, Codex, Cursor, or other client account.
The server owns only the read-only database artifact and web-hosting cost.

## Runtime boundary

- `kreports-mcp` starts `kreports serve-http --public --stateless`.
- The compact SQLite DB and matching `.release.json` manifest are bind-mounted
  read-only at `/data`.
- The root filesystem is read-only; `/tmp` is a tmpfs.
- Caddy exposes only `/mcp` and public liveness `/healthz`. It returns `404`
  for `/readyz`, `/`, and every other path.
- `/readyz` remains an in-container healthcheck. It is never a public data or
  release-inventory endpoint.

The Caddy configuration caps each incoming request body at 256 KB. Configure
Cloudflare's normal DDoS protections for the DNS zone; do not add collector
credentials, a writable DB, or a shared bearer token to this service.

## Deploy a reviewed revision

Run these commands on the Lightsail instance. Start by confirming the existing
checkout is clean. The production checkout intentionally deploys the exact
private-core `origin/main` commit in detached-HEAD mode; this avoids making an
old feature branch the production source.

```bash
cd /srv/kreports/runtime
git status --short --branch
git remote -v
sudo git fetch origin main
sudo git checkout --detach origin/main

sudo docker run --rm \
  -v "$PWD/deploy/lightsail/conf/Caddyfile:/etc/caddy/Caddyfile:ro" \
  caddy:2.11.4-alpine \
  caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile

sudo docker compose \
  --env-file deploy/lightsail/production.env \
  -f deploy/lightsail/compose.yaml config
sudo docker compose \
  --env-file deploy/lightsail/production.env \
  -f deploy/lightsail/compose.yaml up -d --build --force-recreate
```

The first public deployment must remove any old token line from
`deploy/lightsail/production.env` before the final command. `--public` rejects
an inherited `KREPORTS_MCP_TOKEN` so a previous shared secret cannot silently
remain active:

```bash
cd /srv/kreports/runtime
grep -n '^KREPORTS_MCP_TOKEN=' deploy/lightsail/production.env || true
sudo sed -i '/^KREPORTS_MCP_TOKEN=/d' deploy/lightsail/production.env
sudo chmod 600 deploy/lightsail/production.env
```

`production.env` contains only the build SHA in this deployment; do not put a
DB URL, a DART key, raw-storage configuration, or client credential there.

## Verify the rollout

```bash
cd /srv/kreports/runtime
sudo docker compose \
  --env-file deploy/lightsail/production.env \
  -f deploy/lightsail/compose.yaml ps
sudo docker compose \
  --env-file deploy/lightsail/production.env \
  -f deploy/lightsail/compose.yaml exec -T kreports-mcp \
  python -m kreports.deployment_healthcheck

curl -fsS https://mcp.dartmcp.com/healthz
curl -sS -o /dev/null -w '%{http_code}\n' https://mcp.dartmcp.com/readyz
curl -sS -o /dev/null -w '%{http_code}\n' https://mcp.dartmcp.com/
```

Expected results: the Compose service is `healthy`; the internal healthcheck
exits `0`; public `/healthz` returns JSON; external `/readyz` and `/` return
`404`. A bare request to `/mcp` may produce an MCP transport/protocol error,
but must not require bearer authentication. Verify actual tool discovery from a
remote MCP client using `https://mcp.dartmcp.com/mcp` with no headers.

## Publish a new database snapshot

Do not update the database in place and do not connect the public service to a
maintainer or Google Drive path. Build and verify a new release artifact in the
private collector pipeline, copy the DB plus its matching release manifest to
`data/staging`, and promote the pair atomically into `data/active`. Then run
the deployment verification above. Until the promotion and healthcheck succeed,
the service keeps serving the previous mounted snapshot.
