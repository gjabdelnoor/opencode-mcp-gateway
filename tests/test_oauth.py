"""Tests for OAuth endpoints."""

import pytest
from starlette.testclient import TestClient
from unittest.mock import patch, MagicMock
import asyncio
import importlib


TEST_PUBLIC_BASE_URL = "https://mcp.example.com"


class TestOAuthEndpoints:
    """Test cases for OAuth authorization server endpoints."""

    @pytest.fixture
    def client(self):
        """Create a test client for the gateway."""
        with patch.dict(
            "os.environ",
            {
                "MCP_AUTH_TOKEN": "test-token-12345",
                "PUBLIC_BASE_URL": TEST_PUBLIC_BASE_URL,
            },
        ):
            from main import create_fastmcp, main as main_module
            from starlette.routing import Route

            mcp = create_fastmcp()
            mcp_app = mcp.http_app(path="/mcp")

            from main import (
                handle_health,
                handle_oauth_authorize,
                handle_oauth_authorize_post,
                handle_oauth_token,
                handle_oauth_discovery,
                handle_protected_resource,
            )

            mcp_app.add_route("/health", handle_health, methods=["GET"])
            mcp_app.add_route(
                "/.well-known/oauth-authorization-server",
                handle_oauth_discovery,
                methods=["GET"],
            )
            mcp_app.add_route(
                "/.well-known/oauth-authorization-server/mcp",
                handle_oauth_discovery,
                methods=["GET"],
            )
            mcp_app.add_route(
                "/.well-known/oauth-protected-resource",
                handle_protected_resource,
                methods=["GET"],
            )
            mcp_app.add_route("/authorize", handle_oauth_authorize, methods=["GET"])
            mcp_app.add_route(
                "/oauth/authorize", handle_oauth_authorize, methods=["GET"]
            )
            mcp_app.add_route(
                "/oauth/authorize", handle_oauth_authorize_post, methods=["POST"]
            )
            mcp_app.add_route("/oauth/token", handle_oauth_token, methods=["POST"])
            mcp_app.add_route("/mcp/authorize", handle_oauth_authorize, methods=["GET"])
            mcp_app.add_route(
                "/mcp/oauth/authorize", handle_oauth_authorize, methods=["GET"]
            )
            mcp_app.add_route(
                "/mcp/oauth/authorize", handle_oauth_authorize_post, methods=["POST"]
            )
            mcp_app.add_route("/mcp/oauth/token", handle_oauth_token, methods=["POST"])

            return TestClient(mcp_app, raise_server_exceptions=False)

    @pytest.fixture
    def client_with_allowed_client_ids(self):
        """Create a test client with additional allowed OAuth client IDs."""
        with patch.dict(
            "os.environ",
            {
                "MCP_AUTH_TOKEN": "test-token-12345",
                "PUBLIC_BASE_URL": TEST_PUBLIC_BASE_URL,
                "MCP_ALLOWED_CLIENT_IDS": "claude-desktop,opencode-mcp-gateway",
            },
        ):
            import main

            importlib.reload(main)

            mcp = main.create_fastmcp()
            mcp_app = mcp.http_app(path="/mcp")
            mcp_app.add_route("/health", main.handle_health, methods=["GET"])
            mcp_app.add_route(
                "/.well-known/oauth-authorization-server",
                main.handle_oauth_discovery,
                methods=["GET"],
            )
            mcp_app.add_route(
                "/.well-known/oauth-authorization-server/mcp",
                main.handle_oauth_discovery,
                methods=["GET"],
            )
            mcp_app.add_route(
                "/.well-known/oauth-protected-resource",
                main.handle_protected_resource,
                methods=["GET"],
            )
            mcp_app.add_route(
                "/authorize", main.handle_oauth_authorize, methods=["GET"]
            )
            mcp_app.add_route(
                "/oauth/authorize", main.handle_oauth_authorize, methods=["GET"]
            )
            mcp_app.add_route(
                "/oauth/authorize", main.handle_oauth_authorize_post, methods=["POST"]
            )
            mcp_app.add_route("/oauth/token", main.handle_oauth_token, methods=["POST"])
            return TestClient(mcp_app, raise_server_exceptions=False)

    def test_oauth_discovery_claude(self, client):
        """Test OAuth discovery endpoint for Claude."""
        response = client.get("/.well-known/oauth-authorization-server")

        assert response.status_code == 200
        data = response.json()
        assert data["issuer"] == TEST_PUBLIC_BASE_URL
        assert data["authorization_endpoint"] == f"{TEST_PUBLIC_BASE_URL}/authorize"
        assert data["token_endpoint"] == f"{TEST_PUBLIC_BASE_URL}/oauth/token"
        assert "client_secret_basic" in data["token_endpoint_auth_methods_supported"]
        assert "mcp" in data["scopes_supported"]

    def test_oauth_discovery_chatgpt(self, client):
        """Test OAuth discovery endpoint for ChatGPT (with /mcp suffix)."""
        response = client.get("/.well-known/oauth-authorization-server/mcp")

        assert response.status_code == 200
        data = response.json()
        assert data["issuer"] == f"{TEST_PUBLIC_BASE_URL}/mcp"
        assert data["authorization_endpoint"] == f"{TEST_PUBLIC_BASE_URL}/mcp/authorize"
        assert data["token_endpoint"] == f"{TEST_PUBLIC_BASE_URL}/mcp/oauth/token"

    def test_protected_resource_endpoint(self, client):
        """Test protected resource metadata endpoint (RFC 9728)."""
        response = client.get("/.well-known/oauth-protected-resource")

        assert response.status_code == 200
        data = response.json()
        assert data["resource"] == TEST_PUBLIC_BASE_URL
        assert TEST_PUBLIC_BASE_URL in data["authorization_servers"]
        assert "bearer_methods_supported" in data

    def test_authorize_page(self, client):
        """Test authorization page renders correctly."""
        response = client.get(
            "/authorize?client_id=opencode-mcp-gateway&redirect_uri=https://example.com/callback&state=abc123&scope=mcp"
        )

        assert response.status_code == 200
        assert "Authorize Claude Code" in response.text
        assert "opencode-mcp-gateway" in response.text
        assert 'action="/oauth/authorize"' in response.text

    def test_authorize_invalid_client_id(self, client):
        """Test authorization page with invalid client_id."""
        response = client.get(
            "/authorize?client_id=invalid&redirect_uri=https://example.com/callback"
        )

        assert response.status_code == 400
        assert "Invalid client_id" in response.text

    def test_authorize_allows_configured_additional_client_id(
        self, client_with_allowed_client_ids
    ):
        """Test authorization page accepts configured additional client IDs."""
        response = client_with_allowed_client_ids.get(
            "/authorize?client_id=claude-desktop&redirect_uri=https://example.com/callback&state=abc123&scope=mcp"
        )

        assert response.status_code == 302
        assert response.headers["location"].startswith(
            "https://example.com/callback?code="
        )

    def test_authorize_approve(self, client):
        """Test authorization approval."""
        response = client.post(
            "/oauth/authorize",
            data={
                "action": "approve",
                "client_id": "opencode-mcp-gateway",
                "redirect_uri": "https://example.com/callback",
                "state": "abc123",
                "code": "test-code-123",
                "code_challenge": "test-challenge",
                "code_challenge_method": "S256",
                "scope": "mcp",
            },
            allow_redirects=False,
        )

        assert response.status_code == 302
        assert "code=" in response.headers["location"]
        assert "state=abc123" in response.headers["location"]

    def test_authorize_deny(self, client):
        """Test authorization denial."""
        response = client.post(
            "/oauth/authorize",
            data={
                "action": "deny",
                "redirect_uri": "https://example.com/callback",
                "state": "abc123",
            },
            allow_redirects=False,
        )

        assert response.status_code == 302
        assert "error=access_denied" in response.headers["location"]

    def test_token_exchange_json(self, client):
        """Test token exchange with JSON body."""
        with patch.dict("os.environ", {"MCP_AUTH_TOKEN": "test-token-12345"}):
            with patch(
                "main.auth_codes",
                {
                    "test-code-123": {
                        "client_id": "opencode-mcp-gateway",
                        "code_challenge": "test-challenge",
                        "code_challenge_method": "S256",
                        "scope": "mcp",
                        "expires": asyncio.get_event_loop().time() + 300,
                    }
                },
            ):
                import hashlib, base64

                code_verifier = "test-verifier"
                verifier_hash = hashlib.sha256(code_verifier.encode()).digest()
                expected_challenge = (
                    base64.urlsafe_b64encode(verifier_hash).decode().rstrip("=")
                )

                with patch.dict(
                    "main.auth_codes",
                    {
                        "test-code-123": {
                            "client_id": "opencode-mcp-gateway",
                            "code_challenge": expected_challenge,
                            "code_challenge_method": "S256",
                            "scope": "mcp",
                            "expires": asyncio.get_event_loop().time() + 300,
                        }
                    },
                ):
                    response = client.post(
                        "/oauth/token",
                        json={
                            "grant_type": "authorization_code",
                            "code": "test-code-123",
                            "code_verifier": code_verifier,
                        },
                    )

                    assert response.status_code == 200
                    data = response.json()
                    assert data["access_token"] == "test-token-12345"
                    assert data["token_type"] == "Bearer"

    def test_token_exchange_form_encoded(self, client):
        """Test token exchange with form-encoded body (ChatGPT compatibility)."""
        with patch.dict("os.environ", {"MCP_AUTH_TOKEN": "test-token-12345"}):
            response = client.post(
                "/oauth/token",
                data={
                    "grant_type": "client_credentials",
                    "client_id": "opencode-mcp-gateway",
                    "client_secret": "test-token-12345",
                },
            )

            assert response.status_code == 200
            data = response.json()
            assert data["access_token"] == "test-token-12345"
            assert data["token_type"] == "Bearer"

    def test_token_invalid_grant(self, client):
        """Test token exchange with invalid code."""
        with patch.dict("os.environ", {"MCP_AUTH_TOKEN": "test-token-12345"}):
            response = client.post(
                "/oauth/token",
                json={"grant_type": "authorization_code", "code": "invalid-code"},
            )

            assert response.status_code == 400
            assert response.json()["error"] == "invalid_grant"

    def test_token_client_credentials(self, client):
        """Test client credentials grant."""
        with patch.dict("os.environ", {"MCP_AUTH_TOKEN": "test-token-12345"}):
            response = client.post(
                "/oauth/token",
                json={
                    "grant_type": "client_credentials",
                    "client_id": "opencode-mcp-gateway",
                    "client_secret": "test-token-12345",
                },
            )

            assert response.status_code == 200
            data = response.json()
            assert data["access_token"] == "test-token-12345"
            assert data["expires_in"] == 86400

    def test_mcp_endpoint_requires_auth(self, client):
        """Test that MCP endpoint requires authentication."""
        response = client.get("/mcp")

        assert response.status_code == 401
        assert response.json()["error"] == "invalid_token"

    def test_mcp_endpoint_with_valid_token(self, client):
        """Test MCP endpoint with valid auth token."""
        with patch.dict("os.environ", {"MCP_AUTH_TOKEN": "test-token-12345"}):
            response = client.get(
                "/mcp", headers={"Authorization": "Bearer test-token-12345"}
            )

            assert response.status_code == 200

    def test_chatgpt_oauth_flow(self, client):
        """Test full ChatGPT OAuth flow."""
        with patch.dict("os.environ", {"MCP_AUTH_TOKEN": "test-token-12345"}):
            discovery_response = client.get(
                "/.well-known/oauth-authorization-server/mcp"
            )
            assert discovery_response.status_code == 200

            auth_response = client.get(
                "/mcp/authorize?client_id=opencode-mcp-gateway&redirect_uri=https://chatgpt.com/callback&state=xyz"
            )
            assert auth_response.status_code == 200
            assert "Authorize Claude Code" in auth_response.text

            token_response = client.post(
                "/mcp/oauth/token",
                data={
                    "grant_type": "client_credentials",
                    "client_id": "opencode-mcp-gateway",
                    "client_secret": "test-token-12345",
                },
            )
            assert token_response.status_code == 200
            assert "access_token" in token_response.json()
