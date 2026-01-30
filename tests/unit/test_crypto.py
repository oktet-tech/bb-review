"""Tests for password encryption/decryption utilities."""

from pathlib import Path

import pytest

from bb_review.crypto import (
    decrypt_password,
    decrypt_password_from_file,
    encrypt_password,
    encrypt_password_to_file,
)


class TestEncryptDecrypt:
    """Tests for encrypt/decrypt functions."""

    def test_encrypt_decrypt_roundtrip(self):
        """Encrypt then decrypt returns original."""
        password = "my-secret-password"
        token = "encryption-token-12345"

        encrypted = encrypt_password(password, token)
        decrypted = decrypt_password(encrypted, token)

        assert decrypted == password

    def test_different_passwords(self):
        """Different passwords produce different ciphertext."""
        token = "encryption-token"

        encrypted1 = encrypt_password("password1", token)
        encrypted2 = encrypt_password("password2", token)

        assert encrypted1 != encrypted2

    def test_different_tokens(self):
        """Different tokens produce different ciphertext."""
        password = "my-password"

        encrypted1 = encrypt_password(password, "token1")
        encrypted2 = encrypt_password(password, "token2")

        assert encrypted1 != encrypted2

    def test_wrong_token_fails(self):
        """Decrypt with wrong token raises ValueError."""
        password = "my-secret-password"
        correct_token = "correct-token"
        wrong_token = "wrong-token"

        encrypted = encrypt_password(password, correct_token)

        with pytest.raises(ValueError, match="Failed to decrypt"):
            decrypt_password(encrypted, wrong_token)

    def test_corrupted_data_fails(self):
        """Decrypt corrupted data raises ValueError."""
        with pytest.raises(ValueError, match="Failed to decrypt"):
            decrypt_password("not-valid-encrypted-data", "any-token")

    def test_empty_password(self):
        """Empty password encrypts and decrypts correctly."""
        password = ""
        token = "token"

        encrypted = encrypt_password(password, token)
        decrypted = decrypt_password(encrypted, token)

        assert decrypted == password

    def test_unicode_password(self):
        """Unicode password encrypts and decrypts correctly."""
        password = "password-with-unicode-\u00e9\u00e8\u00ea"
        token = "token"

        encrypted = encrypt_password(password, token)
        decrypted = decrypt_password(encrypted, token)

        assert decrypted == password


class TestFileOperations:
    """Tests for file-based encrypt/decrypt."""

    def test_file_operations(self, tmp_path: Path):
        """Write encrypted, read decrypted."""
        password = "file-test-password"
        token = "file-test-token"
        file_path = tmp_path / "password.enc"

        encrypt_password_to_file(password, token, file_path)
        decrypted = decrypt_password_from_file(file_path, token)

        assert decrypted == password

    def test_file_permissions(self, tmp_path: Path):
        """Check file permissions are 0600."""
        password = "test"
        token = "token"
        file_path = tmp_path / "password.enc"

        encrypt_password_to_file(password, token, file_path)

        # Check permissions (owner read/write only)
        mode = file_path.stat().st_mode
        assert mode & 0o777 == 0o600

    def test_creates_parent_directories(self, tmp_path: Path):
        """Creates parent directories if needed."""
        password = "test"
        token = "token"
        file_path = tmp_path / "nested" / "dir" / "password.enc"

        encrypt_password_to_file(password, token, file_path)

        assert file_path.exists()
        decrypted = decrypt_password_from_file(file_path, token)
        assert decrypted == password

    def test_file_not_found(self, tmp_path: Path):
        """Error when password file doesn't exist."""
        file_path = tmp_path / "nonexistent.enc"

        with pytest.raises(FileNotFoundError, match="Password file not found"):
            decrypt_password_from_file(file_path, "token")

    def test_overwrites_existing(self, tmp_path: Path):
        """Overwrites existing file."""
        token = "token"
        file_path = tmp_path / "password.enc"

        # Write first password
        encrypt_password_to_file("password1", token, file_path)
        # Overwrite with second password
        encrypt_password_to_file("password2", token, file_path)

        decrypted = decrypt_password_from_file(file_path, token)
        assert decrypted == "password2"

    def test_strips_whitespace(self, tmp_path: Path):
        """Strips whitespace from file content."""
        password = "test"
        token = "token"
        file_path = tmp_path / "password.enc"

        # Encrypt and write
        encrypt_password_to_file(password, token, file_path)

        # Add whitespace to file
        content = file_path.read_text()
        file_path.write_text(f"  {content}  \n")

        # Should still decrypt correctly
        decrypted = decrypt_password_from_file(file_path, token)
        assert decrypted == password
