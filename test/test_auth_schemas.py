
import pytest
from pydantic import ValidationError

from app.schemas.auth import LoginRequest, SignUpRequest


def valid_signup_data():
    return {
        "email": "  WAREN@example.com ",
        "password": "A-Strong-Password-2026!",
        "confirm_password": "A-Strong-Password-2026!",
        "first_name": " Waren ",
        "last_name": " Odhiambo ",
        "phone_number": "+254712345678",
        "country_code": "ke",
    }


def test_signup_normalizes_input():
    request = SignUpRequest(**valid_signup_data())

    assert request.email == "waren@example.com"
    assert request.first_name == "Waren"
    assert request.last_name == "Odhiambo"
    assert request.country_code == "KE"


def test_signup_rejects_mismatched_passwords():
    data = valid_signup_data()
    data["confirm_password"] = "A-Different-Password-2026!"

    with pytest.raises(ValidationError):
        SignUpRequest(**data)


def test_signup_rejects_short_password():
    data = valid_signup_data()
    data["password"] = "short"
    data["confirm_password"] = "short"

    with pytest.raises(ValidationError):
        SignUpRequest(**data)


def test_signup_rejects_invalid_phone():
    data = valid_signup_data()
    data["phone_number"] = "0712345678"

    with pytest.raises(ValidationError):
        SignUpRequest(**data)


def test_login_accepts_existing_credentials_shape():
    request = LoginRequest(
        email="WAREN@example.com",
        password="Existing-Password!",
    )

    assert request.email == "waren@example.com"
    assert request.password == "Existing-Password!"


def test_login_rejects_invalid_email():
    with pytest.raises(ValidationError):
        LoginRequest(
            email="not-an-email",
            password="Existing-Password!",
        )
