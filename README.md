# OpenCode MCP Gateway

> [!WARNING]
> This project exposes remote shell execution, PTY control, session steering, and agent-driven code execution on the machine where it runs.
> Treat it like a personal-use remote code execution service.
> If this gateway is compromised, an attacker may be able to read files, execute commands, access credentials, damage data, or pivot deeper into your environment.
> Do not expose it to untrusted users. Use strong secrets. Keep the origin machine locked down.

This repository exposes a local OpenCode server as a remote MCP server for Claude and ChatGPT using OAuth and Cloudflare Tunnel.

The deployment model this repo is optimized for is:

- Ubuntu desktop or laptop
- OpenCode running locally on `127.0.0.1:9999`
- This gateway running locally on `127.0.0.1:3001`
- Cloudflare Tunnel providing the public HTTPS endpoint
- No VPS required

## Tested Status

This setup has been exercised against a real desktop-origin deployment behind Cloudflare Tunnel.

Validated:

- public OAuth discovery for Claude and ChatGPT
- Claude remote MCP OAuth handshake
- ChatGPT OAuth handshake
- protected resource metadata discovery
- unauthorized MCP `WWW-Authenticate` discovery flow
- session tools
- PTY tools
- direct bash tools

Latest smoke test status: `20/20` tool paths working on the live deployment.

Important implementation fixes in this repo:

- Claude-compatible `WWW-Authenticate` handling on `/mcp` `401` responses
- protected resource metadata advertising the actual MCP resource URL
- auth-code validation for `redirect_uri` and `resource`
- websocket-backed PTY I/O for `bash_write` and `bash_read`
- session prompt handling via `prompt_async` plus polling instead of relying on empty immediate message responses
- optional per-mode default model overrides so the gateway can bypass broken OpenCode defaults

## What This Repo Does

It sits between remote MCP clients and a local OpenCode server.

High-level flow:

```text
Claude / ChatGPT -> Cloudflare -> cloudflared tunnel -> gateway -> OpenCode
```

Concrete example:

```text
Claude / ChatGPT -> https://mcp.example.com/mcp
                    -> Cloudflare Tunnel
                    -> http://127.0.0.1:3001
                    -> http://127.0.0.1:9999
```

## Read This First

You need OpenCode installed and working before this gateway can do anything useful.

OpenCode docs:

- Intro: `https://opencode.ai/docs/`
- Providers: `https://opencode.ai/docs/providers/`
- Server mode: `https://opencode.ai/docs/server/`

If you want the full expanded walkthrough in this repo, read:

- `docs/ubuntu-cloudflare-desktop-setup.md`

## Quick Setup

### 1. Install Ubuntu packages

```bash
sudo apt update
sudo apt install -y curl git python3 python3-pip python3-venv
```

### 2. Install and configure OpenCode

Install OpenCode:

```bash
curl -fsSL https://opencode.ai/install | bash
```

Then follow the OpenCode docs to configure a provider and start the local server:

```bash
opencode serve --hostname 127.0.0.1 --port 9999
```

Leave that running.

### 3. Clone this repo and install dependencies

```bash
git clone https://github.com/gjabdelnoor/opencode-mcp-gateway-cloudflare-desktop.git
cd opencode-mcp-gateway-cloudflare-desktop
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 4. Create `.env`

```bash
cp .env.example .env
```

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

Notes:

- `PUBLIC_BASE_URL` must be the final external HTTPS URL.
- `DEFAULT_PLANNING_MODEL` and `DEFAULT_BUILDING_MODEL` are strongly recommended if your OpenCode default models are not actually usable with your account.

### 5. Install `cloudflared`

```bash
curl -L --output cloudflared.deb https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64.deb
sudo dpkg -i cloudflared.deb
rm cloudflared.deb
cloudflared --version
```

### 6. Add your domain to Cloudflare and create a tunnel

Authenticate:

```bash
cloudflared tunnel login
```

Create the tunnel:

```bash
cloudflared tunnel create opencode-mcp-gateway
```

Create the DNS route:

```bash
cloudflared tunnel route dns opencode-mcp-gateway mcp.example.com
```

### 7. Create `~/.cloudflared/config.yml`

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

### 8. Start the gateway

```bash
source .venv/bin/activate
python main.py
```

### 9. Start the tunnel

```bash
cloudflared tunnel run opencode-mcp-gateway
```

At that point you should have three live processes:

1. `opencode serve --hostname 127.0.0.1 --port 9999`
2. `python main.py`
3. `cloudflared tunnel run opencode-mcp-gateway`

## Verify The Deployment

Public checks:

```bash
curl https://mcp.example.com/.well-known/oauth-authorization-server
curl https://mcp.example.com/.well-known/oauth-authorization-server/mcp
curl https://mcp.example.com/.well-known/oauth-protected-resource
curl -D - -o /dev/null https://mcp.example.com/mcp
```

What you want to see:

- OAuth issuer: `https://mcp.example.com`
- token endpoint: `https://mcp.example.com/oauth/token`
- protected resource: `https://mcp.example.com/mcp`
- unauthenticated `/mcp` returns `401` with `WWW-Authenticate` including `resource_metadata`

## Connect ChatGPT Or Claude

MCP server URL:

```text
https://mcp.example.com/mcp
```

OAuth discovery URLs:

- Claude: `https://mcp.example.com/.well-known/oauth-authorization-server`
- ChatGPT: `https://mcp.example.com/.well-known/oauth-authorization-server/mcp`

Manual OAuth values when needed:

- OAuth Client ID: `opencode-mcp-gateway`
- OAuth Client Secret: your `MCP_AUTH_TOKEN`
- Token auth method: `client_secret_post`
- Scope: `mcp`

This repo does not implement dynamic client registration. Manual client configuration is the expected path.

## Multiple Concurrent Agents

If you want several Claude or ChatGPT conversations controlling separate agents at once, run multiple gateway processes instead of sharing a single gateway instance.

Recommended layout:

- `mcp1.example.com -> localhost:3001`
- `mcp2.example.com -> localhost:3002`
- `mcp3.example.com -> localhost:3003`
- `mcp4.example.com -> localhost:3004`
- `mcp5.example.com -> localhost:3005`
- `mcp6.example.com -> localhost:3006`

Each instance should have its own:

- `PUBLIC_BASE_URL`
- `GATEWAY_PORT`
- `MCP_AUTH_TOKEN`
- optional `DEFAULT_PLANNING_MODEL`
- optional `DEFAULT_BUILDING_MODEL`

This repo includes `scripts/run-gateway-instance.sh` for per-instance startup with dedicated env files.

## Configuration

| Variable | Description |
|---|---|
| `MCP_AUTH_TOKEN` | Bearer secret for OAuth token exchange and MCP access |
| `MCP_CLIENT_ID` | Main OAuth client ID accepted by the gateway |
| `MCP_ALLOWED_CLIENT_IDS` | Optional comma-separated allowlist of additional client IDs |
| `PUBLIC_BASE_URL` | External HTTPS base URL advertised in OAuth metadata |
| `OPENCODE_HOST` | OpenCode origin host |
| `OPENCODE_PORT` | OpenCode origin port |
| `GATEWAY_PORT` | Gateway listen port |
| `ENABLE_RAW_BASH` | Enables direct `bash` and `bash_exec` tools |
| `DEFAULT_PLANNING_MODEL` | Optional fallback model used for planning-mode sessions |
| `DEFAULT_BUILDING_MODEL` | Optional fallback model used for building-mode sessions |

## Troubleshooting

### ChatGPT says the server URL is invalid

Use the full MCP URL:

```text
https://mcp.example.com/mcp
```

Do not enter only the hostname.

### Claude OAuth fails after the browser redirect

Check all of these:

- `PUBLIC_BASE_URL` is correct
- protected resource metadata returns `https://mcp.example.com/mcp`
- `GET /mcp` without auth returns `401` with `WWW-Authenticate`
- Claude is pointed at `https://mcp.example.com/mcp`
- the OAuth client secret exactly matches `MCP_AUTH_TOKEN`

### Cloudflare returns `502`

Usually one of these:

- OpenCode is not running
- `python main.py` is not running
- `cloudflared` is not running
- the tunnel points at the wrong local port

Local checks:

```bash
curl http://127.0.0.1:9999/global/health
curl http://127.0.0.1:3001/health
```

### `session_create` or `send_message` looks stalled

This is often not an OAuth problem.

Check:

```bash
curl http://127.0.0.1:9999/session/status
```

If OpenCode is retrying an unsupported model, set gateway defaults such as:

```bash
DEFAULT_PLANNING_MODEL=opencode/minimax-m2.5-free
DEFAULT_BUILDING_MODEL=openai/gpt-5.4-mini
```

### Planning mode refuses command execution

That is expected behavior.

Planning mode is for analysis and planning. Use building mode for command execution and editing tasks.

### `bash_write` and `bash_read` return noisy terminal output

That is expected.

PTY output is real terminal output and can contain ANSI escape sequences, shell prompt control codes, and cursor state markers.

### Several bots interfere with each other

Use separate gateway instances on separate hostnames and ports.

Sharing one in-memory gateway process between many active bots is riskier than isolating them.

## Appendix: Theoretical Free Setup Without Personal DNS

This is not tested in this repo.

If you want a completely free path, the theoretical option is to use an ephemeral `trycloudflare.com` hostname instead of a personal domain.

The rough flow would be:

1. Start OpenCode locally
2. Start this gateway locally
3. Run a quick tunnel command
4. Use the temporary hostname as `PUBLIC_BASE_URL`

Example:

```bash
cloudflared tunnel --url http://127.0.0.1:3001
```

Why this is not recommended for stable use:

- hostname changes across reconnects
- OAuth issuer URL changes break client setup
- reconnecting may invalidate saved connector config
- it is worse for repeatable setup and long-term reliability

Use it only for experimentation.

## Detailed Guide

For the full Ubuntu-from-scratch walkthrough, use:

- `docs/ubuntu-cloudflare-desktop-setup.md`

## Questions Or Security Concerns

If you have questions, comments, setup issues, or serious security concerns, contact `@isnotgabe` on Discord.
