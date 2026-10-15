"""The web app's side of auth: it signs a short-lived token for the person signed in.

The browser never holds a long-lived API key. It asks Mosswood's web app for a token that lives five
minutes, and sends it to the gateway. The web app keeps the private key; the gateway only ever sees the
public half, as a JWKS, so a leaked gateway config cannot mint tokens.
"""

import json
import time
from pathlib import Path

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from mosswood.config import AUDIENCE, ISSUER, OUT, TOKEN_SECONDS

KEY_FILE = OUT / "keys" / "signing.pem"
KEY_ID = "mosswood-web-1"


def signing_key(path: Path = KEY_FILE) -> rsa.RSAPrivateKey:
    """Load the web app's private key, making one on first use. It never leaves `out/keys`."""
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        path.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                           serialization.NoEncryption()))
        path.chmod(0o600)
    return serialization.load_pem_private_key(path.read_bytes(), password=None)


def jwks(path: Path = KEY_FILE) -> dict:
    """The public key set the gateway checks signatures against."""
    public = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(signing_key(path).public_key()))
    return {"keys": [{**public, "kid": KEY_ID, "alg": "RS256", "use": "sig"}]}


def issue(tenant_id: str, user_id: str, seconds: int = TOKEN_SECONDS, now: float | None = None,
          path: Path = KEY_FILE) -> str:
    """Sign a token for one person in one workspace. `seconds` below zero makes an expired one for the tests."""
    issued = int(now if now is not None else time.time())
    claims = {"iss": ISSUER, "aud": AUDIENCE, "sub": user_id, "tenant": tenant_id,
              "iat": issued, "exp": issued + seconds}
    return jwt.encode(claims, signing_key(path), algorithm="RS256", headers={"kid": KEY_ID})


def read(token: str, path: Path = KEY_FILE) -> dict:
    """Check a token the way the gateway does. Only the tests call this; in production Envoy does it."""
    return jwt.decode(token, signing_key(path).public_key(), algorithms=["RS256"], audience=AUDIENCE, issuer=ISSUER)
