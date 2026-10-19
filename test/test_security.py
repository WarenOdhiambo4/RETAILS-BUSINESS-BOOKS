
from app.security import (
    create_verification_digest,
    generate_verification_code,
    hash_password,
    verify_code_digest,
    verify_password,
)


def test_verification_code_generation():
    code = generate_verification_code()

    assert len(code) == 6
    assert code.isdigit()


def test_verification_digest():
    user_id = 15
    code = generate_verification_code()

    digest = create_verification_digest(user_id, code)

    assert len(digest) == 32
    assert verify_code_digest(user_id, code, digest)
    assert not verify_code_digest(user_id + 1, code, digest)
    assert not verify_code_digest(user_id, "000000", digest) or code == "000000"


def test_password_hashing():
    password = "Example-Strong-Password-2026!"

    password_hash = hash_password(password)

    assert password_hash != password
    assert verify_password(password, password_hash)
    assert not verify_password("Wrong-Password", password_hash)
