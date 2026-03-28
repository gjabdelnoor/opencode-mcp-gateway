# OpenCode MCP Gateway

Exposes OpenCode as a remote MCP server for Claude Code via `mcp.homunculi.cloud`.

## Architecture

```
┌─────────────┐         ┌──────────────────┐         ┌─────────────────┐
│Claude Code  │──HTTPS──│  Docker Caddy    │──HTTP───│  MCP Gateway   │
│(Researcher) │◀──SSE───│  mcp.homunculi. │         │  localhost:3001 │
│             │         │  cloud:443       │         │  (Python)       │
└─────────────┘         └──────────────────┘         └─────────────────┘
                                                          │
                                                          │ HTTP
                                                          ▼
                                               ┌─────────────────┐
                                               │  OpenCode API  │
                                               │  localhost:9999 │
                                               └─────────────────┘
```

## Features

- **OAuth 2.0 Authorization Server** - Works with Claude Code and ChatGPT Plus
- **16 MCP Tools** - Session management, messaging, PTY terminals
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

### Bash (Claude's Direct Terminal)
| Tool | Description |
|------|-------------|
| `bash_create` | Create PTY terminal |
| `bash_read` | Read PTY output |
| `bash_resize` | Resize terminal |
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

## Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `MCP_AUTH_TOKEN` | (auto-generated) | Bearer token |
| `OPENCODE_HOST` | localhost | OpenCode API host |
| `OPENCODE_PORT` | 9999 | OpenCode API port |
| `GATEWAY_PORT` | 3001 | Gateway HTTP port |

## OAuth Endpoints

- Claude: `https://mcp.homunculi.cloud/.well-known/oauth-authorization-server`
- ChatGPT: `https://mcp.homunculi.cloud/.well-known/oauth-authorization-server/mcp`

## Running Tests

```bash
pip install pytest pytest-asyncio pytest-httpx
pytest tests/
```
