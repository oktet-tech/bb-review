"""Simple encryption utilities for storing passwords."""

import base64
import hashlib
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken


def _derive_key(token: str) -> bytes:
    """Derive a Fernet-compatible key from an arbitrary token.

    Uses SHA256 to hash the token, then base64 encodes for Fernet.
    """
    hash_bytes = hashlib.sha256(token.encode()).digest()
    return base64.urlsafe_b64encode(hash_bytes)


def encrypt_password(password: str, token: str) -> str:
    """Encrypt a password using the given token as the key.

    Args:
        password: The password to encrypt.
        token: The token to use as encryption key (e.g., RB API token).

    Returns:
        Base64-encoded encrypted password.
    """
    key = _derive_key(token)
    fernet = Fernet(key)
    encrypted = fernet.encrypt(password.encode())
    return encrypted.decode()


def decrypt_password(encrypted: str, token: str) -> str:
    """Decrypt a password using the given token as the key.

    Args:
        encrypted: The base64-encoded encrypted password.
        token: The token used as encryption key.

    Returns:
        The decrypted password.

    Raises:
        ValueError: If decryption fails (wrong key or corrupted data).
    """
    key = _derive_key(token)
    fernet = Fernet(key)
    try:
        decrypted = fernet.decrypt(encrypted.encode())
        return decrypted.decode()
    except InvalidToken as err:
        raise ValueError("Failed to decrypt password - wrong token or corrupted data") from err


def encrypt_password_to_file(password: str, token: str, file_path: Path) -> None:
    """Encrypt a password and write it to a file.

    Args:
        password: The password to encrypt.
        token: The token to use as encryption key.
        file_path: Path to write the encrypted password.
    """
    encrypted = encrypt_password(password, token)
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_text(encrypted)
    # Set restrictive permissions
    file_path.chmod(0o600)


def decrypt_password_from_file(file_path: Path, token: str) -> str:
    """Read and decrypt a password from a file.

    Args:
        file_path: Path to the encrypted password file.
        token: The token used as encryption key.

    Returns:
        The decrypted password.
    """
    if not file_path.exists():
        raise FileNotFoundError(f"Password file not found: {file_path}")

    encrypted = file_path.read_text().strip()
    return decrypt_password(encrypted, token)
