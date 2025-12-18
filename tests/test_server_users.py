"""Tests for user profile API endpoints."""

import json
from unittest.mock import MagicMock

import pytest

# Skip if flask not available
pytest.importorskip("flask")

from gptme.server.api_v2_users import (
    decrypt_token,
    encrypt_token,
    users_api,
)


class TestTokenEncryption:
    """Test token encryption/decryption."""

    @pytest.fixture
    def encryption_key(self):
        """Generate a test encryption key."""
        import base64
        import os

        key = os.urandom(32)
        return base64.b64encode(key).decode("utf-8")

    def test_encrypt_decrypt_roundtrip(self, encryption_key, monkeypatch):
        """Test that encryption and decryption are reversible."""
        monkeypatch.setenv("GPTME_ENCRYPTION_KEY", encryption_key)

        # Clear cached key
        import gptme.server.api_v2_users as users_module

        users_module._encryption_key = None

        token = "ghp_test_token_12345"
        user_id = "user-123-abc"

        encrypted = encrypt_token(token, user_id)
        assert encrypted is not None
        assert encrypted != token

        decrypted = decrypt_token(encrypted, user_id)
        assert decrypted == token

    def test_different_users_different_ciphertext(self, encryption_key, monkeypatch):
        """Test that different users get different encrypted values."""
        monkeypatch.setenv("GPTME_ENCRYPTION_KEY", encryption_key)

        import gptme.server.api_v2_users as users_module

        users_module._encryption_key = None

        token = "ghp_same_token"

        encrypted1 = encrypt_token(token, "user-1")
        encrypted2 = encrypt_token(token, "user-2")

        assert encrypted1 != encrypted2

    def test_decrypt_wrong_user_fails(self, encryption_key, monkeypatch):
        """Test that decrypting with wrong user fails."""
        monkeypatch.setenv("GPTME_ENCRYPTION_KEY", encryption_key)

        import gptme.server.api_v2_users as users_module

        users_module._encryption_key = None

        token = "ghp_secret_token"
        encrypted = encrypt_token(token, "user-1")
        assert encrypted is not None  # Encryption should succeed

        # Try decrypting with different user
        decrypted = decrypt_token(encrypted, "user-2")
        assert decrypted is None

    def test_encryption_without_key(self, monkeypatch):
        """Test that encryption fails gracefully without key."""
        monkeypatch.delenv("GPTME_ENCRYPTION_KEY", raising=False)

        import gptme.server.api_v2_users as users_module

        users_module._encryption_key = None

        result = encrypt_token("token", "user")
        assert result is None


class TestUserAPI:
    """Test user API endpoints."""

    @pytest.fixture
    def app(self, monkeypatch):
        """Create test Flask app with auth disabled."""
        import flask

        # Disable auth for testing
        monkeypatch.setenv("GPTME_DISABLE_AUTH", "true")

        # Re-import to pick up env change
        import gptme.server.auth as auth_module

        auth_module._auth_enabled = False

        app = flask.Flask(__name__)
        app.register_blueprint(users_api)
        app.config["TESTING"] = True
        return app

    @pytest.fixture
    def client(self, app):
        """Create test client."""
        return app.test_client()

    def test_save_token_missing_auth(self, client):
        """Test that saving token requires authentication."""
        response = client.post(
            "/api/v2/user/github-token",
            json={"token": "ghp_test"},
            headers={"Authorization": "Bearer test-token"},
        )
        # Without auth setup, should fail
        assert response.status_code in [401, 500]

    def test_save_token_invalid_format(self, client, monkeypatch):
        """Test that invalid token format is rejected."""
        # Mock user extraction
        monkeypatch.setattr(
            "gptme.server.api_v2_users.get_user_from_request",
            lambda: {"id": "test-user"},
        )

        response = client.post(
            "/api/v2/user/github-token",
            json={"token": "invalid_token_format"},
            headers={"Authorization": "Bearer test-token"},
        )
        assert response.status_code == 400
        data = json.loads(response.data)
        assert "Invalid GitHub token format" in data.get("error", "")

    def test_save_token_valid_format(self, client, monkeypatch):
        """Test saving a valid token format (mocked storage)."""
        # Mock user extraction
        monkeypatch.setattr(
            "gptme.server.api_v2_users.get_user_from_request",
            lambda: {"id": "test-user"},
        )

        # Mock encryption (return dummy value)
        monkeypatch.setattr(
            "gptme.server.api_v2_users.encrypt_token", lambda t, u: "encrypted_value"
        )

        # Mock Supabase client
        mock_supabase = MagicMock()
        mock_supabase.table.return_value.update.return_value.eq.return_value.execute.return_value.data = [
            {"id": "test-user"}
        ]
        monkeypatch.setattr(
            "gptme.server.api_v2_users.get_supabase_client", lambda: mock_supabase
        )

        response = client.post(
            "/api/v2/user/github-token",
            json={"token": "ghp_valid_token_12345"},
            headers={"Authorization": "Bearer test-token"},
        )
        assert response.status_code == 200
        data = json.loads(response.data)
        assert data.get("status") == "saved"

    def test_delete_token(self, client, monkeypatch):
        """Test deleting a token."""
        # Mock user extraction
        monkeypatch.setattr(
            "gptme.server.api_v2_users.get_user_from_request",
            lambda: {"id": "test-user"},
        )

        # Mock Supabase client
        mock_supabase = MagicMock()
        mock_supabase.table.return_value.update.return_value.eq.return_value.execute.return_value.data = [
            {"id": "test-user"}
        ]
        monkeypatch.setattr(
            "gptme.server.api_v2_users.get_supabase_client", lambda: mock_supabase
        )

        response = client.delete(
            "/api/v2/user/github-token", headers={"Authorization": "Bearer test-token"}
        )
        assert response.status_code == 200
        data = json.loads(response.data)
        assert data.get("status") == "deleted"

    def test_get_token_status(self, client, monkeypatch):
        """Test checking token status."""
        # Mock user extraction
        monkeypatch.setattr(
            "gptme.server.api_v2_users.get_user_from_request",
            lambda: {"id": "test-user"},
        )

        # Mock Supabase client - token configured
        mock_supabase = MagicMock()
        mock_supabase.table.return_value.select.return_value.eq.return_value.single.return_value.execute.return_value.data = {
            "github_token_encrypted": "some_encrypted_value"
        }
        monkeypatch.setattr(
            "gptme.server.api_v2_users.get_supabase_client", lambda: mock_supabase
        )

        response = client.get(
            "/api/v2/user/github-token", headers={"Authorization": "Bearer test-token"}
        )
        assert response.status_code == 200
        data = json.loads(response.data)
        assert data.get("configured") is True
