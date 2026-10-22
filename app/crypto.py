"""At-rest encryption for GitLab tokens and webhook secrets (Fernet, APP_ENCRYPTION_KEY)."""

from cryptography.fernet import Fernet, InvalidToken

from app.config import get_settings


class SecretBox:
    def __init__(self, key: str) -> None:
        try:
            self._fernet = Fernet(key)
        except ValueError as exc:
            raise ValueError("APP_ENCRYPTION_KEY must be a urlsafe base64 Fernet key") from exc

    def encrypt(self, plaintext: str) -> str:
        return self._fernet.encrypt(plaintext.encode()).decode()

    def decrypt(self, token: str) -> str:
        try:
            return self._fernet.decrypt(token.encode()).decode()
        except InvalidToken as exc:
            # Never include the token: it may be a real secret encrypted with another key.
            raise ValueError(
                "cannot decrypt secret: wrong APP_ENCRYPTION_KEY or corrupt data"
            ) from exc


def secret_box() -> SecretBox:
    return SecretBox(get_settings().app_encryption_key.get_secret_value())
