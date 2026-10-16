"""The owner's sign-in passphrase, stored only as a scrypt hash.

Create the hash (the passphrase is read without echo and never stored):

    python -m auth.passphrase            # prints OWNER_PASSPHRASE_HASH=scrypt$...
"""
from __future__ import annotations

import base64
import getpass
import hashlib
import hmac
import secrets

_N, _R, _P = 2**14, 8, 1


def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode().rstrip("=")


def _unb64(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def hash_passphrase(passphrase: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(passphrase.encode(), salt=salt, n=_N, r=_R, p=_P, dklen=32)
    return f"scrypt${_N}${_R}${_P}${_b64(salt)}${_b64(digest)}"


def verify_passphrase(passphrase: str, stored: str) -> bool:
    try:
        scheme, n, r, p, salt, digest = stored.split("$")
        if scheme != "scrypt":
            return False
        actual = hashlib.scrypt(passphrase.encode(), salt=_unb64(salt), n=int(n), r=int(r), p=int(p),
                                dklen=len(_unb64(digest)))
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(actual, _unb64(digest))


if __name__ == "__main__":
    first = getpass.getpass("Owner passphrase: ")
    if len(first) < 12:
        raise SystemExit("Use at least 12 characters.")
    if getpass.getpass("Again: ") != first:
        raise SystemExit("The passphrases don't match.")
    print(f"OWNER_PASSPHRASE_HASH={hash_passphrase(first)}")
