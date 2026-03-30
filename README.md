# OpenCode MCP Gateway

Exposes OpenCode as a remote MCP server for Claude Code and ChatGPT Plus.

This fork is aimed at the simplest real-world deployment model:

- Ubuntu machine
- OpenCode running locally
- Cloudflare Tunnel for the public HTTPS endpoint
- no VPS required

If you want the full from-scratch guide, start here:

- `docs/ubuntu-cloudflare-desktop-setup.md`

OpenCode docs you should read first:

- Intro: `https://opencode.ai/docs/`
- Providers: `https://opencode.ai/docs/providers/`
- Server mode: `https://opencode.ai/docs/server/`

If you want the full step-by-step guide, common pitfalls, and an untested all-free alternative path, read:

- `docs/ubuntu-cloudflare-desktop-setup.md`

## Architecture

```
┌─────────────┐         ┌──────────────────┐         ┌─────────────────┐
│Claude/      │──HTTPS──│ Cloudflare Edge  │──Tunnel─│  MCP Gateway    │
│ChatGPT      │◀──SSE───│  mcp.example.com │         │  localhost:3001 │
└─────────────┘         └──────────────────┘         └─────────────────┘
                                                           │
                                                           │ HTTP
                                                           ▼
                                                ┌─────────────────┐
                                                │  OpenCode API   │
                                                │  localhost:9999 │
                                                └─────────────────┘
```

## Features

- **OAuth 2.0 Authorization Server** - Works with Claude Code and ChatGPT Plus
- **Expanded Bash Surface** - Raw command execution + PTY lifecycle tools
- **Model Switching** - Support for multiple AI models per session
- **Protected Resource Metadata** - RFC 9728 compliance

## Available MCP Tools

### Session Management
| Tool | Description |
|------|-------------|
| `session_list` | List all OpenCode sessions |
| `session_create` | Create a new session |
| `session_get` | Get session details |
| `session_delete` | Delete a session |
| `session_fork` | Fork a session |
| `read_session` | Read session's full details |
| `switch_session` | Switch to a different session |
| `switch_model` | Set model for session |
| `get_active_session` | Get currently active session |

### Messaging (Agent Steering)
| Tool | Description |
|------|-------------|
| `message_send` | Send prompt to OpenCode agent |
| `message_abort` | Abort ongoing generation |

### Human Input Queue
| Tool | Description |
|------|-------------|
| `question_list` | List pending interactive questions |
| `question_reply` | Answer a queued question request |
| `question_reject` | Reject a queued question request |
| `permission_list` | List pending permission requests |
| `permission_reply` | Respond to queued permission request (`once`, `always`, `reject`) |

### Bash (Claude's Direct Terminal)
| Tool | Description |
|------|-------------|
| `bash` | Execute raw shell command (`command`, `timeout`, `workdir`, `description`) |
| `bash_exec` | Alias of `bash` |
| `bash_create` | Create PTY terminal |
| `bash_list` | List PTY sessions |
| `bash_get` | Get PTY details |
| `bash_read` | Read PTY output |
| `bash_resize` | Resize terminal |
| `bash_update` | Update PTY title/size |
| `bash_write` | Write input to PTY |
| `bash_close` | Close PTY |

### Status
| Tool | Description |
|------|-------------|
| `status` | Health check |

## Quick Start

```bash
cd /home/opencode/mcp-gateway
source .venv/bin/activate
pip install -r requirements.txt
python main.py
```

## Recommended Path

For a desktop or laptop setup with a paid domain and a free Cloudflare account, follow the full guide in:

- `docs/ubuntu-cloudflare-desktop-setup.md`

## Cloudflare Tunnel Setup

This repo is already compatible with Cloudflare Tunnel. The key requirement is to set `PUBLIC_BASE_URL` to the public HTTPS URL that Claude or ChatGPT will use for OAuth metadata and callbacks.

You do not need a VPS. You can run the gateway and `cloudflared` from a desktop or laptop that stays powered on when you want the MCP server available.

Example target hostname:

```text
https://mcp.example.com
```

### 1. Point your domain at Cloudflare

Use a domain you own, move its DNS to Cloudflare if needed, and create a hostname such as `mcp.example.com`.

### 2. Create a Cloudflare Tunnel

Install `cloudflared`, authenticate, and create a tunnel:

```bash
cloudflared tunnel login
cloudflared tunnel create opencode-mcp-gateway
```

Then create the DNS route:

```bash
cloudflared tunnel route dns opencode-mcp-gateway mcp.example.com
```

### 3. Configure the tunnel ingress

Use `deploy/cloudflared/config.yml.example` as your starting point and install it as `/etc/cloudflared/opencode-mcp-gateway.yml`.

Important values to change:

- `YOUR_TUNNEL_ID`
- `credentials-file`
- `hostname: mcp.example.com`

### 4. Configure the gateway

Set these in your `.env` file:

```bash
MCP_AUTH_TOKEN=replace-with-a-long-random-secret
MCP_CLIENT_ID=opencode-mcp-gateway
MCP_ALLOWED_CLIENT_IDS=opencode-mcp-gateway
PUBLIC_BASE_URL=https://mcp.example.com
OPENCODE_HOST=localhost
OPENCODE_PORT=9999
GATEWAY_PORT=3001
ENABLE_RAW_BASH=true
```

`PUBLIC_BASE_URL` should always be the external HTTPS URL served through Cloudflare. That keeps OAuth discovery, token, and authorization URLs correct even though the Python app listens only on localhost.

### 5. Run both services

Start the gateway with the existing systemd unit, then run `cloudflared` with the new unit template in `deploy/systemd/cloudflared-opencode-mcp-gateway.service`.

```bash
sudo cp deploy/systemd/opencode-mcp-gateway.service /etc/systemd/system/opencode-mcp-gateway.service
sudo cp deploy/systemd/cloudflared-opencode-mcp-gateway.service /etc/systemd/system/cloudflared-opencode-mcp-gateway.service
sudo systemctl daemon-reload
sudo systemctl enable --now opencode-mcp-gateway
sudo systemctl enable --now cloudflared-opencode-mcp-gateway
```

For a desktop or laptop setup without systemd services, run the gateway locally and start the tunnel directly:

```bash
export PUBLIC_BASE_URL=https://mcp1.kvcache.blog
export CLOUDFLARE_TUNNEL_TOKEN=replace-with-your-tunnel-token
python main.py
```

In another terminal:

```bash
./scripts/run-local-cloudflare-tunnel.sh
```

### 6. Verify OAuth metadata

Once the tunnel is live, these should resolve publicly:

```text
https://mcp.example.com/.well-known/oauth-authorization-server
https://mcp.example.com/.well-known/oauth-authorization-server/mcp
https://mcp.example.com/.well-known/oauth-protected-resource
```

Register these with your clients:

- Claude: `https://mcp.example.com/.well-known/oauth-authorization-server`
- ChatGPT: `https://mcp.example.com/.well-known/oauth-authorization-server/mcp`

## Auto-Restart (systemd)

For VPS reliability, run the gateway as a systemd service with automatic restart.

```bash
cd /home/opencode/mcp-gateway
sudo cp deploy/systemd/opencode-mcp-gateway.service /etc/systemd/system/opencode-mcp-gateway.service
sudo systemctl daemon-reload
sudo systemctl enable --now opencode-mcp-gateway
sudo systemctl status opencode-mcp-gateway --no-pager
```

Useful commands:

```bash
sudo systemctl restart opencode-mcp-gateway
sudo journalctl -u opencode-mcp-gateway -n 100 --no-pager
```

## Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `MCP_AUTH_TOKEN` | (auto-generated) | Bearer token |
| `MCP_CLIENT_ID` | opencode-mcp-gateway | OAuth client ID expected by this gateway |
| `MCP_ALLOWED_CLIENT_IDS` | `MCP_CLIENT_ID` | Comma-separated list of additional OAuth client IDs to accept |
| `OPENCODE_HOST` | localhost | OpenCode API host |
| `OPENCODE_PORT` | 9999 | OpenCode API port |
| `GATEWAY_PORT` | 3001 | Gateway HTTP port |
| `ENABLE_RAW_BASH` | true | Enables direct `bash`/`bash_exec` command tool |
| `PUBLIC_BASE_URL` | (auto-detected) | Override externally visible OAuth base URL; set this when using Cloudflare Tunnel or any reverse proxy |

## Second Connector On Same Domain

You can expose a second gateway on the same domain using a path prefix, for example `/desktop`.

- Run the second gateway with `PUBLIC_BASE_URL=https://mcp.example.com/desktop`
- Route `https://mcp.example.com/desktop/*` to that second gateway, stripping the `/desktop` prefix at the reverse proxy
- Register ChatGPT connector URL as `https://mcp.example.com/desktop/mcp`

## Security Note

`bash`/`bash_exec` is remote code execution on the host machine. Keep this gateway personal-use and protect it with strong credentials.

## OAuth Endpoints

- Claude: `https://mcp.example.com/.well-known/oauth-authorization-server`
- ChatGPT: `https://mcp.example.com/.well-known/oauth-authorization-server/mcp`

## Running Tests

```bash
pip install pytest pytest-asyncio pytest-httpx
pytest tests/
```
