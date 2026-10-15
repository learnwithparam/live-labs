"""Basalt's staff sign-in, standing in for the company's identity provider (Okta, Entra, Google).

A staff agent carries a short-lived token that says who the person is and which team they are in.
The gateway checks the signature against the public keys (a JWKS file) and reads the team from the
token, so an agent cannot claim a team its person is not in. The private key never leaves this module.
"""

import json
import time
from pathlib import Path

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from jwt.algorithms import RSAAlgorithm

from basalt.catalogue import staff
from basalt.config import OUT

ISSUER = "https://id.basaltpay.com"
AUDIENCE = "mcp.basaltpay.internal"
KEY_ID = "staff-2026-10"
KEYS = OUT / "identity"
TOKEN_LIFETIME = 15 * 60  # seconds: long enough for one agent session, short enough to be worth little if leaked


def signing_key() -> rsa.RSAPrivateKey:
    """Make the key pair once per checkout and publish only the public half, as an identity provider does."""
    private_file, jwks_file = KEYS / "signing-key.pem", KEYS / "jwks.json"
    if private_file.exists():
        return serialization.load_pem_private_key(private_file.read_bytes(), password=None)
    KEYS.mkdir(parents=True, exist_ok=True)
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_file.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                               serialization.NoEncryption()))
    private_file.chmod(0o600)
    public = json.loads(RSAAlgorithm.to_jwk(key.public_key()))
    jwks_file.write_text(json.dumps({"keys": [{**public, "kid": KEY_ID, "alg": "RS256", "use": "sig"}]}))
    return key


def jwks_path() -> Path:
    signing_key()
    return KEYS / "jwks.json"


def token_for(email: str) -> str:
    """A signed token for one person. The team comes from the staff directory, never from the request."""
    person = staff().get(email)
    if not person:
        raise SystemExit(f"{email} is not in data/staff.toml.")
    now = int(time.time())
    claims = {"iss": ISSUER, "aud": AUDIENCE, "sub": email, "name": person["name"], "team": person["team"],
              "iat": now, "exp": now + TOKEN_LIFETIME}
    return jwt.encode(claims, signing_key(), algorithm="RS256", headers={"kid": KEY_ID})
