import pytest
from cryptography.fernet import Fernet

from app.crypto import SecretBox


def test_round_trip_and_ciphertext_hides_plaintext() -> None:
    box = SecretBox(Fernet.generate_key().decode())
    token = box.encrypt("glpat-abc123")
    assert "glpat-abc123" not in token
    assert box.decrypt(token) == "glpat-abc123"


def test_wrong_key_fails_without_leaking_ciphertext() -> None:
    token = SecretBox(Fernet.generate_key().decode()).encrypt("glpat-abc123")
    with pytest.raises(ValueError, match="cannot decrypt") as excinfo:
        SecretBox(Fernet.generate_key().decode()).decrypt(token)
    assert token not in str(excinfo.value)


def test_invalid_key_is_rejected() -> None:
    with pytest.raises(ValueError, match="Fernet key"):
        SecretBox("not-a-key")
