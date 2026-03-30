# Ubuntu Desktop + Cloudflare Setup

This guide walks through a full setup from a fresh Ubuntu machine to a public MCP endpoint that Claude or ChatGPT can use over OAuth.

The target architecture is:

```text
Claude / ChatGPT -> Cloudflare -> cloudflared tunnel -> this gateway -> OpenCode server
```

This is a desktop-first setup. You do not need a VPS. Your MCP server is available only while your Ubuntu machine is powered on and running both OpenCode and the gateway.

## Tested Scope

This guide is based on a setup that has been exercised against real public Cloudflare-hosted endpoints.

Validated pieces:

- local OpenCode origin on Ubuntu
- local Python gateway behind Cloudflare Tunnel
- public OAuth discovery endpoints
- ChatGPT OAuth flow against the public endpoint
- Claude remote MCP OAuth flow against the public endpoint

The biggest interoperability fixes that made Claude work reliably were:

- adding `WWW-Authenticate` with `resource_metadata` on unauthorized `/mcp` responses
- advertising the actual MCP resource URL in protected resource metadata
- validating `redirect_uri` and `resource` during auth-code exchange
- tolerating either `https://host` or `https://host/mcp` style resource values when clients vary

## What You Need

- Ubuntu machine
- Free Cloudflare account
- A domain you already own
- The domain delegated to Cloudflare nameservers
- LLM provider access for OpenCode

## Before You Start

You need OpenCode installed and configured first. This repository depends on a running OpenCode HTTP server.

Read these OpenCode docs before continuing:

- Intro: `https://opencode.ai/docs/`
- Provider configuration: `https://opencode.ai/docs/providers/`
- Server mode: `https://opencode.ai/docs/server/`

The short version is:

1. Install OpenCode
2. Configure a provider and API key
3. Start `opencode serve` on a stable localhost port

## 1. Install Base Packages on Ubuntu

```bash
sudo apt update
sudo apt install -y curl git python3 python3-pip python3-venv
```

## 2. Install and Configure OpenCode

Install OpenCode using the official installer:

```bash
curl -fsSL https://opencode.ai/install | bash
```

Then follow the official OpenCode docs to:

1. Sign in or configure your provider
2. Add your model credentials
3. Verify `opencode` runs locally

Start the OpenCode server on `127.0.0.1:9999` so it matches this gateway's default configuration:

```bash
opencode serve --hostname 127.0.0.1 --port 9999
```

Leave that process running.

If you want to use a different port, update `OPENCODE_PORT` in the gateway `.env` file later.

## 3. Clone This Repository

```bash
git clone https://github.com/YOUR_GITHUB_USER/YOUR_REPO_NAME.git
cd YOUR_REPO_NAME
```

## 4. Install Gateway Dependencies

Using a virtual environment is the safest approach on Ubuntu:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## 5. Create Gateway Configuration

Copy the example env file and edit it:

```bash
cp .env.example .env
```

Set:

- `MCP_AUTH_TOKEN` to a long random secret
- `PUBLIC_BASE_URL` to your future public hostname, for example `https://mcp.example.com`
- `OPENCODE_HOST=127.0.0.1`
- `OPENCODE_PORT=9999`

Example:

```bash
MCP_AUTH_TOKEN=replace-with-a-long-random-secret
MCP_CLIENT_ID=opencode-mcp-gateway
MCP_ALLOWED_CLIENT_IDS=opencode-mcp-gateway
PUBLIC_BASE_URL=https://mcp.example.com
OPENCODE_HOST=127.0.0.1
OPENCODE_PORT=9999
GATEWAY_PORT=3001
ENABLE_RAW_BASH=true
DEFAULT_PLANNING_MODEL=opencode/minimax-m2.5-free
DEFAULT_BUILDING_MODEL=openai/gpt-5.4-mini
```

Those last two model overrides are optional, but strongly recommended if your OpenCode default planning or building models are not actually usable with your account. In one real-world setup, OpenCode kept retrying an unsupported default model and the gateway appeared to "stall" until these overrides were set.

## 6. Start the Gateway Locally

```bash
source .venv/bin/activate
python main.py
```

This should listen on `http://127.0.0.1:3001`.

In another terminal, verify the local discovery endpoint:

```bash
curl http://127.0.0.1:3001/.well-known/oauth-authorization-server
```

The returned JSON should advertise your `PUBLIC_BASE_URL`, not `localhost`.

## 7. Add Your Domain to Cloudflare

If your domain is not already using Cloudflare DNS:

1. Add the site in Cloudflare
2. Update your registrar nameservers to the Cloudflare nameservers shown in the dashboard
3. Wait for the zone to become active

You do not need a paid Cloudflare plan for this setup.

## 8. Install cloudflared on Ubuntu

Cloudflare publishes an Ubuntu `.deb` package. Install it with:

```bash
curl -L --output cloudflared.deb https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64.deb
sudo dpkg -i cloudflared.deb
rm cloudflared.deb
```

Verify it installed:

```bash
cloudflared --version
```

## 9. Authenticate cloudflared

```bash
cloudflared tunnel login
```

This opens a browser window. Pick the Cloudflare account and zone that owns your domain.

## 10. Create the Tunnel

```bash
cloudflared tunnel create opencode-mcp-gateway
```

This prints a tunnel ID and stores credentials under `~/.cloudflared/`.

## 11. Create the Public DNS Record

Pick a hostname such as `mcp.example.com` and route it to the tunnel:

```bash
cloudflared tunnel route dns opencode-mcp-gateway mcp.example.com
```

## 12. Create the Tunnel Config

Create `~/.cloudflared/config.yml`:

```yaml
tunnel: YOUR_TUNNEL_ID
credentials-file: /home/YOUR_USER/.cloudflared/YOUR_TUNNEL_ID.json

ingress:
  - hostname: mcp.example.com
    service: http://127.0.0.1:3001
    originRequest:
      httpHostHeader: mcp.example.com
  - service: http_status:404
```

Update:

- `YOUR_TUNNEL_ID`
- `YOUR_USER`
- `mcp.example.com`

## 13. Start the Tunnel

```bash
cloudflared tunnel run opencode-mcp-gateway
```

Leave it running.

At this point you should have three live processes:

1. `opencode serve --hostname 127.0.0.1 --port 9999`
2. `python main.py`
3. `cloudflared tunnel run opencode-mcp-gateway`

## 14. Verify the Public OAuth Endpoints

Test these from the same machine or anywhere on the internet:

```bash
curl https://mcp.example.com/.well-known/oauth-authorization-server
curl https://mcp.example.com/.well-known/oauth-authorization-server/mcp
curl https://mcp.example.com/.well-known/oauth-protected-resource
```

The main checks are:

- `issuer` is `https://mcp.example.com`
- `authorization_endpoint` is `https://mcp.example.com/authorize`
- `token_endpoint` is `https://mcp.example.com/oauth/token`
- protected resource metadata returns `resource: https://mcp.example.com/mcp`
- `GET https://mcp.example.com/mcp` without a bearer token returns `401` and includes a `WWW-Authenticate` header with `resource_metadata`

Example checks:

```bash
curl -i https://mcp.example.com/.well-known/oauth-protected-resource
curl -D - -o /dev/null https://mcp.example.com/mcp
```

## 15. Connect ChatGPT or Claude

For clients that want the actual MCP server URL, use:

```text
https://mcp.example.com/mcp
```

For OAuth discovery endpoints, use:

- Claude: `https://mcp.example.com/.well-known/oauth-authorization-server`
- ChatGPT: `https://mcp.example.com/.well-known/oauth-authorization-server/mcp`

If you are configuring ChatGPT manually, the usual values are:

- OAuth Client ID: `opencode-mcp-gateway`
- OAuth Client Secret: the value of `MCP_AUTH_TOKEN`
- Token auth method: `client_secret_post`
- Scope: `mcp`

If you are configuring Claude manually, use:

- MCP Server URL: `https://mcp.example.com/mcp`
- OAuth Client ID: `opencode-mcp-gateway`
- OAuth Client Secret: the value of `MCP_AUTH_TOKEN`

Claude can discover the rest from the MCP server.

For Claude or other OAuth clients that send a different `client_id`, either:

- configure the client to use `opencode-mcp-gateway`, or
- add that client ID to `MCP_ALLOWED_CLIENT_IDS`

Example:

```bash
MCP_ALLOWED_CLIENT_IDS=opencode-mcp-gateway,claude-desktop
```

## Optional: Start Services More Conveniently

This repo includes:

- `scripts/run-local-cloudflare-tunnel.sh`
- `deploy/systemd/opencode-mcp-gateway.service`
- `deploy/systemd/cloudflared-opencode-mcp-gateway.service`

If you want the machine to bring the gateway back automatically after reboot, use the systemd unit files. If you only need it while the desktop is in use, running the three commands manually is enough.

## Running Multiple Gateways For Concurrent Agents

If you want multiple chatbot conversations to drive separate OpenCode agent sessions concurrently, the safest approach is to run multiple gateway processes.

Example layout:

```text
mcp1.example.com -> localhost:3001
mcp2.example.com -> localhost:3002
mcp3.example.com -> localhost:3003
mcp4.example.com -> localhost:3004
mcp5.example.com -> localhost:3005
mcp6.example.com -> localhost:3006
```

Each instance should have its own environment file with at least:

- unique `PUBLIC_BASE_URL`
- unique `GATEWAY_PORT`
- unique `MCP_AUTH_TOKEN`
- optional `DEFAULT_PLANNING_MODEL` and `DEFAULT_BUILDING_MODEL` overrides

Example for a second instance:

```bash
MCP_AUTH_TOKEN=replace-with-another-secret
MCP_CLIENT_ID=opencode-mcp-gateway
MCP_ALLOWED_CLIENT_IDS=opencode-mcp-gateway
PUBLIC_BASE_URL=https://mcp2.example.com
OPENCODE_HOST=127.0.0.1
OPENCODE_PORT=9999
GATEWAY_PORT=3002
ENABLE_RAW_BASH=true
DEFAULT_PLANNING_MODEL=opencode/minimax-m2.5-free
DEFAULT_BUILDING_MODEL=openai/gpt-5.4-mini
```

Then add a matching Cloudflare Tunnel ingress rule:

```yaml
- hostname: mcp2.example.com
  service: http://127.0.0.1:3002
```

Why this is better than one shared gateway:

- each gateway keeps its own in-memory active session state
- OAuth secrets are isolated per connector
- one bot is less likely to interfere with another bot's session selection

## Untested All-Free Alternative

The main guide assumes:

- a free Cloudflare account
- a domain you already pay for

If you want a completely free path, the most obvious hypothetical alternative is to use an ephemeral `trycloudflare.com` hostname instead of your own domain.

That would look roughly like this:

1. Start OpenCode locally
2. Start this gateway locally
3. Run a quick tunnel command that gives you a random public hostname
4. Use that hostname as `PUBLIC_BASE_URL`

Example shape:

```bash
cloudflared tunnel --url http://127.0.0.1:3001
```

Then use the generated hostname as the public base URL.

Important caveats:

- this path is not tested in this repo
- the hostname is temporary and can change
- OAuth clients may not behave well with a changing issuer URL
- reconnects may invalidate previous connector configuration
- it is worse for repeatable setup and long-term reliability

So this may work for experimentation, but it is not the recommended path for a stable ChatGPT or Claude connector.

## Common Pitfalls

### ChatGPT says the server URL is invalid

Use the full MCP URL, not just the hostname:

```text
https://mcp.example.com/mcp
```

Do not enter only `mcp.example.com`.

### OAuth metadata advertises the wrong hostname

Set `PUBLIC_BASE_URL` correctly in `.env`.

If this is wrong, ChatGPT and Claude will discover bad callback and token URLs.

### The gateway starts, but Cloudflare returns 502

Usually one of these:

- `python main.py` is not running
- `cloudflared` is not running
- the tunnel points to the wrong local port
- the gateway crashed on startup

Check:

```bash
curl http://127.0.0.1:3001/health
```

### The gateway cannot talk to OpenCode

Make sure OpenCode is actually serving on `127.0.0.1:9999`:

```bash
curl http://127.0.0.1:9999/global/health
```

If you are using another port, update `.env`.

### ChatGPT OAuth succeeds but tool calls fail

Check that the OAuth client secret in ChatGPT exactly matches `MCP_AUTH_TOKEN`.

### Claude OAuth fails with an invalid client error

This gateway does not implement dynamic client registration.

If Claude sends a different `client_id`, either:

- configure Claude to use `opencode-mcp-gateway`, or
- add Claude's client ID to `MCP_ALLOWED_CLIENT_IDS`

Useful log messages to look for:

- `oauth_authorize_invalid_client`
- `oauth_client_id_mismatch`
- `oauth_invalid_client_for_auth_code`

### Claude says authorization failed even though the login redirect worked

Check these first:

- `GET /.well-known/oauth-protected-resource` returns `resource: https://mcp.example.com/mcp`
- `GET /mcp` without auth returns `401` with a `WWW-Authenticate` header containing `resource_metadata`
- the MCP server URL entered in Claude is `https://mcp.example.com/mcp`
- the OAuth client secret entered in Claude exactly matches `MCP_AUTH_TOKEN`
- `PUBLIC_BASE_URL` is the public HTTPS hostname, not `localhost`

If any of those are wrong, Claude may complete the browser redirect but still fail the connector handshake.

### `session_create` or `send_message` returns no reply or appears stuck

Check `http://127.0.0.1:9999/session/status`.

If you see a retry error like an unsupported model, your OpenCode defaults are the problem, not the OAuth layer.

Typical fix:

```bash
DEFAULT_PLANNING_MODEL=opencode/minimax-m2.5-free
DEFAULT_BUILDING_MODEL=openai/gpt-5.4-mini
```

You can also explicitly switch models per session using the gateway tools.

### `bash_write` or `bash_read` behaves strangely

OpenCode PTY I/O uses a websocket transport, not the plain REST path you might expect.

This fork uses the PTY websocket directly and returns raw terminal output, including ANSI escape sequences and shell prompt control codes. That is expected.

### Multiple bots are stepping on each other

Run separate gateway instances on separate hostnames and ports, for example:

- `mcp1.example.com -> localhost:3001`
- `mcp2.example.com -> localhost:3002`

This is safer than sharing one gateway across several active chatbots.

## What Changed in This Fork

Compared to the upstream repo, this setup-focused variant adds:

- Cloudflare Tunnel deployment examples
- desktop-local tunnel runner script
- configurable OAuth metadata tests using `PUBLIC_BASE_URL`
- `fastmcp` in `requirements.txt` because the code imports it at runtime
- protected resource and `WWW-Authenticate` fixes for Claude-compatible remote MCP OAuth
- optional `MCP_ALLOWED_CLIENT_IDS` for environments with multiple expected OAuth client IDs

## Security Notes

- `MCP_AUTH_TOKEN` protects the token exchange and MCP access path. Treat it like a password.
- `bash` and PTY tools expose remote code execution on your machine. Do not publish this for untrusted users.
- Never commit `.env` files, tunnel tokens, API keys, or Cloudflare credentials.

## Questions or Suggestions

If you have questions, run into setup issues, or want to recommend security or architecture changes, the best way to reach the maintainer is `@isnotgabe` on Discord.
