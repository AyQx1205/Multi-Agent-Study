from app.security import (
    hash_password,
    hash_session_token,
    new_session_token,
    verify_password,
)


def test_password_hash_roundtrip_and_rejects_wrong_password() -> None:
    encoded = hash_password("correct horse battery staple")
    assert encoded.startswith("scrypt$")
    assert verify_password("correct horse battery staple", encoded) is True
    assert verify_password("wrong password", encoded) is False


def test_session_tokens_are_random_and_stored_as_hash() -> None:
    first = new_session_token()
    second = new_session_token()
    assert first != second
    assert hash_session_token(first) == hash_session_token(first)
    assert hash_session_token(first) != first
    assert hash_session_token(first) != hash_session_token(second)