import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings
from django.db import models


class EncryptedCharField(models.CharField):
    """
    Encrypts/decrypts string values at rest using Fernet.
    Stored format: enc::<token>
    """

    prefix = "enc::"

    def _fernet(self):
        # Deterministic key derivation from Django SECRET_KEY for app-level encryption.
        # For stricter controls, rotate with a dedicated env key and keyring strategy.
        secret = (settings.SECRET_KEY or "").encode("utf-8")
        digest = hashlib.sha256(secret).digest()
        key = base64.urlsafe_b64encode(digest)
        return Fernet(key)

    def get_prep_value(self, value):
        value = super().get_prep_value(value)
        if value in (None, ""):
            return value
        value = str(value)
        if value.startswith(self.prefix):
            return value
        token = self._fernet().encrypt(value.encode("utf-8")).decode("utf-8")
        return f"{self.prefix}{token}"

    def from_db_value(self, value, expression, connection):
        return self.to_python(value)

    def to_python(self, value):
        value = super().to_python(value)
        if value in (None, ""):
            return value
        if not isinstance(value, str):
            return value
        if not value.startswith(self.prefix):
            return value
        token = value[len(self.prefix) :]
        try:
            return self._fernet().decrypt(token.encode("utf-8")).decode("utf-8")
        except InvalidToken:
            return ""
