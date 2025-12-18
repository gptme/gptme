"""
User profile API endpoints for gptme server.

Provides endpoints for user-specific settings and integrations,
including GitHub token storage for the hosted service.
"""

import logging
import os
from base64 import b64decode, b64encode

import flask
from flask import jsonify, request

from .auth import require_auth

logger = logging.getLogger(__name__)

users_api = flask.Blueprint("users_api", __name__)

# Encryption key from environment (for token encryption)
_encryption_key: bytes | None = None


def get_encryption_key() -> bytes | None:
    """Get the encryption key from environment.

    Returns:
        32-byte encryption key from GPTME_ENCRYPTION_KEY env var,
        or None if not configured.
    """
    global _encryption_key
    if _encryption_key is None:
        key_b64 = os.environ.get("GPTME_ENCRYPTION_KEY")
        if key_b64:
            try:
                _encryption_key = b64decode(key_b64)
                if len(_encryption_key) != 32:
                    logger.error(
                        "GPTME_ENCRYPTION_KEY must be 32 bytes (base64 encoded)"
                    )
                    _encryption_key = None
            except Exception as e:
                logger.error(f"Invalid GPTME_ENCRYPTION_KEY: {e}")
    return _encryption_key


def encrypt_token(token: str, user_id: str) -> str | None:
    """Encrypt a token for storage.

    Uses Fernet symmetric encryption with an additional
    user_id binding to prevent token swapping.

    Args:
        token: The plaintext token to encrypt
        user_id: User ID for additional binding

    Returns:
        Base64-encoded encrypted token, or None if encryption unavailable
    """
    key = get_encryption_key()
    if not key:
        logger.warning("Encryption key not configured, cannot encrypt token")
        return None

    try:
        # Create a user-specific key derivation
        import hashlib

        from cryptography.fernet import Fernet

        user_key = hashlib.pbkdf2_hmac(
            "sha256", key, user_id.encode("utf-8"), 100000, dklen=32
        )
        f = Fernet(b64encode(user_key))
        encrypted = f.encrypt(token.encode("utf-8"))
        return b64encode(encrypted).decode("utf-8")
    except ImportError:
        logger.warning("cryptography package not installed")
        return None
    except Exception as e:
        logger.error(f"Encryption failed: {e}")
        return None


def decrypt_token(encrypted_b64: str, user_id: str) -> str | None:
    """Decrypt a stored token.

    Args:
        encrypted_b64: Base64-encoded encrypted token
        user_id: User ID for key derivation

    Returns:
        Decrypted token string, or None if decryption fails
    """
    key = get_encryption_key()
    if not key:
        return None

    try:
        import hashlib

        from cryptography.fernet import Fernet

        user_key = hashlib.pbkdf2_hmac(
            "sha256", key, user_id.encode("utf-8"), 100000, dklen=32
        )
        f = Fernet(b64encode(user_key))
        encrypted = b64decode(encrypted_b64)
        decrypted = f.decrypt(encrypted)
        return decrypted.decode("utf-8")
    except Exception as e:
        logger.error(f"Decryption failed: {e}")
        return None


def get_supabase_client():
    """Get Supabase client if configured.

    Returns:
        Supabase client instance, or None if not configured
    """
    supabase_url = os.environ.get("SUPABASE_URL")
    supabase_key = os.environ.get("SUPABASE_SERVICE_KEY")

    if not supabase_url or not supabase_key:
        return None

    try:
        from supabase import create_client

        return create_client(supabase_url, supabase_key)
    except ImportError:
        logger.warning("supabase package not installed")
        return None
    except Exception as e:
        logger.error(f"Failed to create Supabase client: {e}")
        return None


def get_user_from_request() -> dict | None:
    """Extract user information from request.

    For Supabase auth, extracts user from JWT in Authorization header.
    Falls back to extracting from x-user-id header for development.

    Returns:
        Dict with user info including 'id', or None if not authenticated
    """
    # Development mode: use x-user-id header
    user_id = request.headers.get("x-user-id")
    if user_id:
        return {"id": user_id}

    # Production: Validate Supabase JWT
    auth_header = request.headers.get("Authorization")
    if auth_header and auth_header.startswith("Bearer "):
        token = auth_header.split(" ", 1)[1]
        supabase = get_supabase_client()
        if supabase:
            try:
                user = supabase.auth.get_user(token)
                if user and user.user:
                    return {"id": user.user.id}
            except Exception as e:
                logger.warning(f"Failed to validate user token: {e}")

    return None


@users_api.route("/api/v2/user/github-token", methods=["POST"])
@require_auth
def save_github_token():
    """Save user's GitHub Personal Access Token.

    Encrypts the token and stores it in Supabase profiles table.
    The token is bound to the user and cannot be accessed by others.

    Request body:
        {
            "token": "ghp_xxxx..."
        }

    Returns:
        200: Token saved successfully
        400: Invalid request (missing token)
        401: Not authenticated
        500: Storage error
    """
    user = get_user_from_request()
    if not user:
        return jsonify({"error": "User not authenticated"}), 401

    data = request.get_json()
    if not data or not data.get("token"):
        return jsonify({"error": "Token is required"}), 400

    token = data["token"]
    user_id = user["id"]

    # Basic validation - GitHub PATs start with ghp_ or github_pat_
    if not (token.startswith("ghp_") or token.startswith("github_pat_")):
        return jsonify({"error": "Invalid GitHub token format"}), 400

    # Encrypt the token
    encrypted = encrypt_token(token, user_id)
    if not encrypted:
        return jsonify({"error": "Encryption not configured"}), 500

    # Store in Supabase
    supabase = get_supabase_client()
    if not supabase:
        return jsonify({"error": "Database not configured"}), 500

    try:
        # Update user profile with encrypted token
        result = (
            supabase.table("profiles")
            .update({"github_token_encrypted": encrypted})
            .eq("id", user_id)
            .execute()
        )

        if result.data:
            logger.info(f"GitHub token saved for user {user_id[:8]}...")
            return jsonify({"status": "saved"}), 200
        else:
            return jsonify({"error": "User profile not found"}), 404
    except Exception as e:
        logger.error(f"Failed to save GitHub token: {e}")
        return jsonify({"error": "Storage failed"}), 500


@users_api.route("/api/v2/user/github-token", methods=["DELETE"])
@require_auth
def delete_github_token():
    """Delete user's stored GitHub token.

    Removes the encrypted token from the user's profile.

    Returns:
        200: Token deleted successfully
        401: Not authenticated
        500: Storage error
    """
    user = get_user_from_request()
    if not user:
        return jsonify({"error": "User not authenticated"}), 401

    user_id = user["id"]

    supabase = get_supabase_client()
    if not supabase:
        return jsonify({"error": "Database not configured"}), 500

    try:
        result = (
            supabase.table("profiles")
            .update({"github_token_encrypted": None})
            .eq("id", user_id)
            .execute()
        )

        if result.data:
            logger.info(f"GitHub token deleted for user {user_id[:8]}...")
            return jsonify({"status": "deleted"}), 200
        else:
            return jsonify({"error": "User profile not found"}), 404
    except Exception as e:
        logger.error(f"Failed to delete GitHub token: {e}")
        return jsonify({"error": "Deletion failed"}), 500


@users_api.route("/api/v2/user/github-token", methods=["GET"])
@require_auth
def get_github_token_status():
    """Check if user has a GitHub token configured.

    Returns whether a token is stored, not the token itself.

    Returns:
        200: {"configured": true/false}
        401: Not authenticated
        500: Database error
    """
    user = get_user_from_request()
    if not user:
        return jsonify({"error": "User not authenticated"}), 401

    user_id = user["id"]

    supabase = get_supabase_client()
    if not supabase:
        return jsonify({"error": "Database not configured"}), 500

    try:
        result = (
            supabase.table("profiles")
            .select("github_token_encrypted")
            .eq("id", user_id)
            .single()
            .execute()
        )

        if result.data:
            has_token = bool(result.data.get("github_token_encrypted"))
            return jsonify({"configured": has_token}), 200
        else:
            return jsonify({"configured": False}), 200
    except Exception as e:
        logger.error(f"Failed to check GitHub token status: {e}")
        return jsonify({"error": "Database error"}), 500


# Internal function for agent pods to retrieve decrypted token
def get_user_github_token(user_id: str) -> str | None:
    """Retrieve and decrypt a user's GitHub token.

    Internal function for use in agent pod initialization.
    NOT exposed via API for security.

    Args:
        user_id: The user's ID

    Returns:
        Decrypted GitHub token, or None if not found
    """
    supabase = get_supabase_client()
    if not supabase:
        return None

    try:
        result = (
            supabase.table("profiles")
            .select("github_token_encrypted")
            .eq("id", user_id)
            .single()
            .execute()
        )

        if result.data and result.data.get("github_token_encrypted"):
            return decrypt_token(result.data["github_token_encrypted"], user_id)
    except Exception as e:
        logger.error(f"Failed to retrieve GitHub token: {e}")

    return None
