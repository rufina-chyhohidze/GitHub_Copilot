"""Authentication delegates cryptographic checks to an injected verifier."""


def authenticate(token, verify_token):
    claims = verify_token(token)
    if not claims.get("sub"):
        raise ValueError("Missing subject")
    return claims["sub"]
