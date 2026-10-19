
import logging
from datetime import datetime, timedelta
from app.schemas.auth import ForgotPasswordRequest
from app.schemas.auth import ResetPasswordRequest

from app.security import (
    hash_password,
    verify_password_reset_code,
)
from app.security import (
    generate_password_reset_code,
    digest_password_reset_code,
)
from fastapi import APIRouter, Depends, HTTPException, status

from app.schemas.auth import (
    LoginRequest,
    ResendVerificationRequest,
    SignUpRequest,
    VerifyEmailRequest,
)
from app.security import (
    create_session_digest,
    create_verification_digest,
    generate_session_token,
    generate_verification_code,
    hash_password,
    verify_password,
    verify_code_digest,
)
from app.security_dependencies import get_current_user

import httpx
from fastapi import APIRouter, HTTPException, status
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from app.database import engine
from app.schemas.auth import SignUpRequest
from app.security import (
    create_verification_digest,
    generate_verification_code,
    hash_password,
)
from app.security_settings import security_settings


logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/v1/auth",
    tags=["Authentication"],
)

VERIFICATION_EXPIRY_MINUTES = 10
PASSWORD_RESET_EXPIRY_MINUTES = 10
PASSWORD_RESET_COOLDOWN_SECONDS = 60


@router.post("/register", status_code=status.HTTP_201_CREATED)
def register_user(payload: SignUpRequest):
    """
    Create a pending user account and dispatch an email-verification code.
    """

    # 1. Prepare sensitive values in application memory.
    normalized_email = payload.email.casefold()
    password_hash = hash_password(payload.password)

    verification_code = generate_verification_code()

    # We store only the HMAC digest, never the plain verification code.
   

    user_id = None

    # 2. Create the user and verification record atomically.
    try:
        with engine.begin() as connection:
            existing_user = connection.execute(
                text("""
                    SELECT UserID
                    FROM dbo.Users
                    WHERE NormalizedEmail = :normalized_email;
                """),
                {"normalized_email": normalized_email},
            ).first()

            if existing_user is not None:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="An account with this email already exists.",
                )

            user_result = connection.execute(
                text("""
                    INSERT INTO dbo.Users (
                        Email,
                        NormalizedEmail,
                        PasswordHash,
                        FirstName,
                        LastName,
                        PhoneNumber,
                        CountryCode,
                        EmailVerified,
                        AccountStatus
                    )
                    OUTPUT INSERTED.UserID
                    VALUES (
                        :email,
                        :normalized_email,
                        :password_hash,
                        :first_name,
                        :last_name,
                        :phone_number,
                        :country_code,
                        0,
                        N'Pending'
                    );
                """),
                {
                    "email": str(payload.email),
                    "normalized_email": normalized_email,
                    "password_hash": password_hash,
                    "first_name": payload.first_name,
                    "last_name": payload.last_name,
                    "phone_number": payload.phone_number,
                    "country_code": payload.country_code,
                },
            )

            user_id = user_result.scalar_one()

            # Recalculate the digest using the actual database UserID.
            code_digest = create_verification_digest(
                user_id=user_id,
                code=verification_code,
            )

            connection.execute(
                text("""
                    INSERT INTO dbo.EmailVerificationCodes (
                        UserID,
                        CodeDigest,
                        ExpiresAt
                    )
                    VALUES (
                        :user_id,
                        :code_digest,
                        DATEADD(
                            MINUTE,
                            :expiry_minutes,
                            SYSUTCDATETIME()
                        )
                    );
                """),
                {
                    "user_id": user_id,
                    "code_digest": code_digest,
                    "expiry_minutes": VERIFICATION_EXPIRY_MINUTES,
                },
            )

    except HTTPException:
        raise

    except IntegrityError as exc:
        # The database unique constraint remains the final protection
        # against concurrent duplicate registrations.
        logger.warning("Registration rejected by a database constraint.")
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An account with these details already exists.",
        ) from exc

    except SQLAlchemyError as exc:
        logger.exception("Registration database operation failed.")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Registration is temporarily unavailable.",
        ) from exc

    # 3. The transaction has committed. Now contact n8n.
    try:
        with httpx.Client(timeout=10.0) as client:
            response = client.post(
                security_settings.n8n_webhook_url,
                headers={
                    "X-Webhook-Secret": (
                        security_settings.n8n_webhook_secret
                        .get_secret_value()
                    ),
                },
                json={
                    "user_id": user_id,
                    "email": str(payload.email),
                    "first_name": payload.first_name,
                    "verification_code": verification_code,
                    "expires_in_minutes": VERIFICATION_EXPIRY_MINUTES,
                },
            )
            

            response.raise_for_status()
            

    except (httpx.HTTPError, ValueError):
        # Do not log the verification code, password, or webhook secret.
        logger.error(
            "Verification email dispatch failed for user_id=%s.",
            user_id,
        )

        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "Your account was created, but the verification email "
                "could not be sent. Please contact support."
            ),
        )

    # 4. Return only non-sensitive information.
    return {
        "message": (
            "Your account has been created. "
            "Check your email for the verification code."
        ),
        "user_id": user_id,
        "email": str(payload.email),
        "email_verified": False,
        "account_status": "Pending",
    }



SESSION_DURATION_HOURS = 8


@router.post("/login")
def login_user(payload: LoginRequest):
    """Authenticate an active, email-verified user and create a session."""

    normalized_email = str(payload.email).casefold()

    try:
        with engine.begin() as connection:
            user = connection.execute(
                text("""
                    SELECT
                        UserID,
                        PasswordHash,
                        EmailVerified,
                        AccountStatus
                    FROM dbo.Users
                    WHERE NormalizedEmail = :normalized_email;
                """),
                {"normalized_email": normalized_email},
            ).mappings().first()

            # Use the same response for an unknown email and wrong password.
            if user is None or not verify_password(
                payload.password,
                user["PasswordHash"] if user is not None else (
                    "$argon2id$v=19$m=65536,t=3,p=4$"
                    "invalidsalt$invalidhash"
                ),
            ):
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Invalid email or password.",
                    headers={"WWW-Authenticate": "Bearer"},
                )

            if not user["EmailVerified"]:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Verify your email before logging in.",
                )

            if user["AccountStatus"] != "Active":
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="This account is not currently allowed to log in.",
                )

            session_token = generate_session_token()
            token_digest = create_session_digest(session_token)

            connection.execute(
                text("""
                    INSERT INTO dbo.UserSessions (
                        UserID,
                        TokenDigest,
                        ExpiresAt
                    )
                    VALUES (
                        :user_id,
                        :token_digest,
                        DATEADD(
                            HOUR,
                            :duration_hours,
                            SYSUTCDATETIME()
                        )
                    );

                    UPDATE dbo.Users
                    SET LastLoginAt = SYSUTCDATETIME()
                    WHERE UserID = :user_id;
                """),
                {
                    "user_id": user["UserID"],
                    "token_digest": token_digest,
                    "duration_hours": SESSION_DURATION_HOURS,
                },
            )

    except HTTPException:
        raise

    except SQLAlchemyError as exc:
        logger.exception("Login database operation failed.")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Login is temporarily unavailable.",
        ) from exc

    return {
        "access_token": session_token,
        "token_type": "bearer",
        "expires_in": SESSION_DURATION_HOURS * 60 * 60,
    }


@router.post("/logout")
def logout_user(current_user: dict = Depends(get_current_user)):
    """Revoke the session used for this request."""

    try:
        with engine.begin() as connection:
            result = connection.execute(
                text("""
                    UPDATE dbo.UserSessions
                    SET RevokedAt = SYSUTCDATETIME()
                    WHERE SessionID = :session_id
                      AND RevokedAt IS NULL;
                """),
                {"session_id": current_user["SessionID"]},
            )

            if result.rowcount != 1:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="The session is no longer valid.",
                )

    except HTTPException:
        raise

    except SQLAlchemyError as exc:
        logger.exception("Logout database operation failed.")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Logout is temporarily unavailable.",
        ) from exc

    return {"message": "You have been logged out."}

@router.post("/verify-email")
def verify_email(payload: VerifyEmailRequest):
    """Verify an email address using its latest valid code."""

    normalized_email = str(payload.email).casefold()
    outcome = "invalid"

    try:
        with engine.begin() as connection:
            user = connection.execute(
                text("""
                    SELECT UserID, EmailVerified, AccountStatus
                    FROM dbo.Users WITH (UPDLOCK, HOLDLOCK)
                    WHERE NormalizedEmail = :normalized_email;
                """),
                {"normalized_email": normalized_email},
            ).mappings().first()

            if user is not None and user["EmailVerified"]:
                outcome = "already_verified"

            elif user is not None and user["AccountStatus"] == "Pending":
                verification = connection.execute(
                    text("""
                        SELECT TOP (1)
                            VerificationID,
                            CodeDigest,
                            ExpiresAt,
                            AttemptCount,
                            MaxAttempts
                        FROM dbo.EmailVerificationCodes
                            WITH (UPDLOCK, HOLDLOCK)
                        WHERE UserID = :user_id
                          AND UsedAt IS NULL
                          AND RevokedAt IS NULL
                        ORDER BY CreatedAt DESC, VerificationID DESC;
                    """),
                    {"user_id": user["UserID"]},
                ).mappings().first()

                if (
                    verification is not None
                    and verification["AttemptCount"] < verification["MaxAttempts"]
                ):
                    is_unexpired = connection.execute(
                        text("""
                            SELECT CASE
                                WHEN :expires_at > SYSUTCDATETIME()
                                THEN 1 ELSE 0
                            END;
                        """),
                        {"expires_at": verification["ExpiresAt"]},
                    ).scalar_one()

                    if is_unexpired:
                        matches = verify_code_digest(
                            user_id=user["UserID"],
                            submitted_code=payload.verification_code,
                            stored_digest=verification["CodeDigest"],
                        )

                        if not matches:
                            connection.execute(
                                text("""
                                    UPDATE dbo.EmailVerificationCodes
                                    SET AttemptCount = AttemptCount + 1
                                    WHERE VerificationID = :verification_id
                                      AND AttemptCount < MaxAttempts;
                                """),
                                {
                                    "verification_id": verification["VerificationID"]
                                },
                            )
                            # Do not raise here: the failed attempt must commit.
                            outcome = "invalid"
                        else:
                            consumed = connection.execute(
                                text("""
                                    UPDATE dbo.EmailVerificationCodes
                                    SET UsedAt = SYSUTCDATETIME()
                                    WHERE VerificationID = :verification_id
                                      AND UsedAt IS NULL
                                      AND RevokedAt IS NULL
                                      AND ExpiresAt > SYSUTCDATETIME()
                                      AND AttemptCount < MaxAttempts;
                                """),
                                {
                                    "verification_id": verification["VerificationID"]
                                },
                            )

                            if consumed.rowcount == 1:
                                activated = connection.execute(
                                    text("""
                                        UPDATE dbo.Users
                                        SET EmailVerified = 1,
                                            AccountStatus = N'Active',
                                            UpdatedAt = SYSUTCDATETIME()
                                        WHERE UserID = :user_id
                                          AND EmailVerified = 0
                                          AND AccountStatus = N'Pending';
                                    """),
                                    {"user_id": user["UserID"]},
                                )

                                if activated.rowcount == 1:
                                    outcome = "verified"
                                else:
                                    # Unexpected state change: roll back
                                    # the code consumption as well.
                                    raise RuntimeError(
                                        "Account activation state changed unexpectedly."
                                    )

    except SQLAlchemyError as exc:
        logger.exception("Email verification database operation failed.")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Email verification is temporarily unavailable.",
        ) from exc

    except RuntimeError as exc:
        logger.exception("Email verification state transition failed.")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Email verification could not be completed. Please retry.",
        ) from exc

    if outcome == "already_verified":
        return {"message": "This email address is already verified."}

    if outcome == "invalid":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="The verification request is invalid or has expired.",
        )

    return {
        "message": "Your email has been verified. You can now log in.",
        "email_verified": True,
        "account_status": "Active",
    }



@router.post("/resend-verification")
def resend_verification(payload: ResendVerificationRequest):
    cooldown_seconds = 60
    normalized_email = str(payload.email).strip().casefold()

    # Keep the response identical for unknown, verified, and
    # cooldown-limited accounts to reduce email enumeration.
    generic_response = {
        "message": (
            "If the account is eligible, a verification email "
            "will be sent if a new code can be issued."
        )
    }

    verification_code = None
    user_id = None
    first_name = None

    try:
        with engine.begin() as connection:
            # Lock the matching user row while checking eligibility.
            user = connection.execute(
                text("""
                    SELECT
                        UserID,
                        Email,
                        FirstName,
                        EmailVerified,
                        AccountStatus
                    FROM dbo.Users WITH (UPDLOCK, HOLDLOCK)
                    WHERE NormalizedEmail = :normalized_email;
                """),
                {"normalized_email": normalized_email},
            ).mappings().first()

            if (
                user is not None
                and not user["EmailVerified"]
                and user["AccountStatus"] == "Pending"
            ):
                user_id = user["UserID"]
                first_name = user["FirstName"]

                # Check the newest code, including codes that were
                # already used or revoked, so repeated requests cannot
                # bypass the cooldown.
                latest_code = connection.execute(
                    text("""
                        SELECT TOP (1) CreatedAt
                        FROM dbo.EmailVerificationCodes
                            WITH (UPDLOCK, HOLDLOCK)
                        WHERE UserID = :user_id
                        ORDER BY CreatedAt DESC, VerificationID DESC;
                    """),
                    {"user_id": user_id},
                ).mappings().first()

                cooldown_active = False

                if latest_code is not None:
                    cooldown_active = bool(
                        connection.execute(
                            text("""
                                SELECT CASE
                                    WHEN DATEADD(
                                        SECOND,
                                        :cooldown_seconds,
                                        :created_at
                                    ) > SYSUTCDATETIME()
                                    THEN 1
                                    ELSE 0
                                END;
                            """),
                            {
                                "cooldown_seconds": cooldown_seconds,
                                "created_at": latest_code["CreatedAt"],
                            },
                        ).scalar_one()
                    )

                if not cooldown_active:
                    verification_code = generate_verification_code()

                    code_digest = create_verification_digest(
                        user_id=user_id,
                        code=verification_code,
                    )

                    # Invalidate all previously active codes.
                    connection.execute(
                        text("""
                            UPDATE dbo.EmailVerificationCodes
                            SET RevokedAt = SYSUTCDATETIME()
                            WHERE UserID = :user_id
                              AND UsedAt IS NULL
                              AND RevokedAt IS NULL;
                        """),
                        {"user_id": user_id},
                    )

                    # Store only the digest of the new code.
                    connection.execute(
                        text("""
                            INSERT INTO dbo.EmailVerificationCodes (
                                UserID,
                                CodeDigest,
                                ExpiresAt
                            )
                            VALUES (
                                :user_id,
                                :code_digest,
                                DATEADD(
                                    MINUTE,
                                    :expiry_minutes,
                                    SYSUTCDATETIME()
                                )
                            );
                        """),
                        {
                            "user_id": user_id,
                            "code_digest": code_digest,
                            "expiry_minutes": VERIFICATION_EXPIRY_MINUTES,
                        },
                    )

    except SQLAlchemyError as exc:
        logger.exception("Resend verification database operation failed.")
        raise HTTPException(
            status_code=503,
            detail="Verification is temporarily unavailable. Please retry.",
        ) from exc

    # Unknown, verified, non-pending, or cooldown-limited accounts
    # receive the same response and trigger no email.
    if verification_code is None:
        return generic_response

    # The database transaction has committed before the network call.
    try:
        response = httpx.post(
            security_settings.n8n_webhook_url,
            headers={
                "X-Webhook-Secret": (
                    security_settings.n8n_webhook_secret.get_secret_value()
                )
            },
            json={
                "user_id": user_id,
                "email": normalized_email,
                "first_name": first_name,
                "verification_code": verification_code,
                "expires_in_minutes": VERIFICATION_EXPIRY_MINUTES,
            },
            timeout=10.0,
        )
        
        response.raise_for_status()


    except (httpx.HTTPError, ValueError):
        # Do not log the verification code or webhook secret.
        logger.exception(
            "Verification email dispatch failed for user_id=%s.",
            user_id,
        )

        # Keep the response generic to avoid exposing account existence.
        # The code was saved, but delivery needs investigation.
        return generic_response

    return generic_response



@router.post("/forgot-password")
def forgot_password(payload: ForgotPasswordRequest):
    normalized_email = str(payload.email).strip().casefold()

    generic_response = {
        "message": (
            "If an eligible account exists, password reset "
            "instructions will be sent if a new code can be issued."
        )
    }

    reset_code = None
    user_id = None
    first_name = None
    reset_id = None

    try:
        with engine.begin() as connection:
            # Lock the user row while checking eligibility.
            user = connection.execute(
                text("""
                    SELECT
                        UserID,
                        Email,
                        FirstName,
                        EmailVerified,
                        AccountStatus
                    FROM dbo.Users WITH (UPDLOCK, HOLDLOCK)
                    WHERE NormalizedEmail = :normalized_email;
                """),
                {"normalized_email": normalized_email},
            ).mappings().first()

            # Only verified, active accounts can request a reset.
            if (
                user is not None
                and user["EmailVerified"]
                and user["AccountStatus"] == "Active"
            ):
                user_id = user["UserID"]
                first_name = user["FirstName"]

                # Apply cooldown using the newest code, even if it
                # was used or revoked.
                latest_code = connection.execute(
                    text("""
                        SELECT TOP (1) CreatedAt
                        FROM dbo.PasswordResetCodes
                            WITH (UPDLOCK, HOLDLOCK)
                        WHERE UserID = :user_id
                        ORDER BY CreatedAt DESC, PasswordResetID DESC;
                    """),
                    {"user_id": user_id},
                ).mappings().first()

                cooldown_active = False

                if latest_code is not None:
                    cooldown_active = bool(
                        connection.execute(
                            text("""
                                SELECT CASE
                                    WHEN DATEADD(
                                        SECOND,
                                        :cooldown_seconds,
                                        :created_at
                                    ) > SYSUTCDATETIME()
                                    THEN 1
                                    ELSE 0
                                END;
                            """),
                            {
                                "cooldown_seconds":
                                    PASSWORD_RESET_COOLDOWN_SECONDS,
                                "created_at": latest_code["CreatedAt"],
                            },
                        ).scalar_one()
                    )

                if not cooldown_active:
                    reset_code = generate_password_reset_code()

                    code_digest = digest_password_reset_code(
                        user_id=user_id,
                        code=reset_code,
                    )

                    # Invalidate all previously active reset codes.
                    connection.execute(
                        text("""
                            UPDATE dbo.PasswordResetCodes
                            SET RevokedAt = SYSUTCDATETIME()
                            WHERE UserID = :user_id
                              AND UsedAt IS NULL
                              AND RevokedAt IS NULL;
                        """),
                        {"user_id": user_id},
                    )

                    # Store only the digest, never the six-digit code.
                    reset_id = connection.execute(
                        text("""
                            INSERT INTO dbo.PasswordResetCodes (
                                UserID,
                                CodeDigest,
                                ExpiresAt
                            )
                            OUTPUT INSERTED.PasswordResetID
                            VALUES (
                                :user_id,
                                :code_digest,
                                DATEADD(
                                    MINUTE,
                                    :expiry_minutes,
                                    SYSUTCDATETIME()
                                )
                            );
                        """),
                        {
                            "user_id": user_id,
                            "code_digest": code_digest,
                            "expiry_minutes":
                                PASSWORD_RESET_EXPIRY_MINUTES,
                        },
                    ).scalar_one()

    except SQLAlchemyError as exc:
        logger.exception(
            "Forgot-password database operation failed."
        )
        raise HTTPException(
            status_code=503,
            detail="Password reset is temporarily unavailable. Please retry.",
        ) from exc

    # Unknown, ineligible, or cooldown-limited accounts receive
    # the same response and trigger no email.
    if reset_code is None:
        return generic_response

    # Send the reset email only after the database transaction commits.
    try:
        response = httpx.post(
            security_settings.n8n_password_reset_webhook_url,
            headers={
                "X-Webhook-Secret": (
                    security_settings.n8n_webhook_secret.get_secret_value()
                )
            },
            json={
                "user_id": user_id,
                "email": normalized_email,
                "first_name": first_name,
                "reset_code": reset_code,
                "expires_in_minutes": PASSWORD_RESET_EXPIRY_MINUTES,
            },
            timeout=10.0,
        )
        response.raise_for_status()

    except (httpx.HTTPError, ValueError):
        # Never log the reset code or webhook secret.
        logger.exception(
            "Password-reset email dispatch failed for user_id=%s.",
            user_id,
        )

        # Revoke the code if delivery failed, so an undelivered code
        # cannot be used. A cleanup failure is logged separately.
        try:
            with engine.begin() as connection:
                connection.execute(
                    text("""
                        UPDATE dbo.PasswordResetCodes
                        SET RevokedAt = SYSUTCDATETIME()
                        WHERE PasswordResetID = :reset_id
                          AND UsedAt IS NULL
                          AND RevokedAt IS NULL;
                    """),
                    {"reset_id": reset_id},
                )
        except SQLAlchemyError:
            logger.exception(
                "Failed to revoke an undelivered password-reset code "
                "for user_id=%s.",
                user_id,
            )

        return generic_response

    return generic_response




@router.post("/reset-password")
def reset_password(payload: ResetPasswordRequest):
    normalized_email = str(payload.email).strip().casefold()

    # Keep account and code failures indistinguishable.
    invalid_reset = False
    password_updated = False

    try:
        with engine.begin() as connection:
            # Lock the user row to prevent concurrent reset operations
            # for the same account from racing each other.
            user = connection.execute(
                text("""
                    SELECT
                        UserID,
                        EmailVerified,
                        AccountStatus
                    FROM dbo.Users WITH (UPDLOCK, HOLDLOCK)
                    WHERE NormalizedEmail = :normalized_email;
                """),
                {"normalized_email": normalized_email},
            ).mappings().first()

            if (
                user is None
                or not user["EmailVerified"]
                or user["AccountStatus"] != "Active"
            ):
                invalid_reset = True

            else:
                user_id = user["UserID"]

                # Inspect the newest code, not an older code that may
                # previously have been issued.
                reset_record = connection.execute(
                    text("""
                        SELECT TOP (1)
                            PasswordResetID,
                            CodeDigest,
                            ExpiresAt,
                            AttemptCount,
                            MaxAttempts,
                            UsedAt,
                            RevokedAt,
                            CASE
                                WHEN ExpiresAt <= SYSUTCDATETIME()
                                THEN 1
                                ELSE 0
                            END AS IsExpired
                        FROM dbo.PasswordResetCodes
                            WITH (UPDLOCK, HOLDLOCK)
                        WHERE UserID = :user_id
                        ORDER BY CreatedAt DESC, PasswordResetID DESC;
                    """),
                    {"user_id": user_id},
                ).mappings().first()

                if reset_record is None:
                    invalid_reset = True

                elif (
                    reset_record["UsedAt"] is not None
                    or reset_record["RevokedAt"] is not None
                    or reset_record["IsExpired"]
                    or reset_record["AttemptCount"]
                        >= reset_record["MaxAttempts"]
                ):
                    invalid_reset = True

                else:
                    code_is_valid = verify_password_reset_code(
                        user_id=user_id,
                        code=payload.reset_code,
                        stored_digest=reset_record["CodeDigest"],
                    )

                    if not code_is_valid:
                        # Persist the failed attempt. Do not raise an
                        # exception inside the transaction, because that
                        # would roll back this increment.
                        connection.execute(
                            text("""
                                UPDATE dbo.PasswordResetCodes
                                SET AttemptCount = AttemptCount + 1
                                WHERE PasswordResetID = :reset_id
                                  AND AttemptCount < MaxAttempts
                                  AND UsedAt IS NULL
                                  AND RevokedAt IS NULL;
                            """),
                            {
                                "reset_id":
                                    reset_record["PasswordResetID"],
                            },
                        )

                        invalid_reset = True

                    else:
                        # Reuse the application's existing Argon2id
                        # password hashing function.
                        new_password_hash = hash_password(
                            payload.new_password
                        )

                        # Change the password only for an eligible account.
                        user_update = connection.execute(
                            text("""
                                UPDATE dbo.Users
                                SET
                                    PasswordHash = :password_hash,
                                    UpdatedAt = SYSUTCDATETIME()
                                WHERE UserID = :user_id
                                  AND EmailVerified = 1
                                  AND AccountStatus = 'Active';
                            """),
                            {
                                "password_hash": new_password_hash,
                                "user_id": user_id,
                            },
                        )

                        if user_update.rowcount != 1:
                            raise RuntimeError(
                                "Password reset account update failed."
                            )

                        # Consume this code exactly once.
                        code_update = connection.execute(
                            text("""
                                UPDATE dbo.PasswordResetCodes
                                SET UsedAt = SYSUTCDATETIME()
                                WHERE PasswordResetID = :reset_id
                                  AND UsedAt IS NULL
                                  AND RevokedAt IS NULL
                                  AND AttemptCount < MaxAttempts;
                            """),
                            {
                                "reset_id":
                                    reset_record["PasswordResetID"],
                            },
                        )

                        if code_update.rowcount != 1:
                            raise RuntimeError(
                                "Password reset code consumption failed."
                            )

                        # Invalidate any other outstanding reset codes.
                        connection.execute(
                            text("""
                                UPDATE dbo.PasswordResetCodes
                                SET RevokedAt = SYSUTCDATETIME()
                                WHERE UserID = :user_id
                                  AND PasswordResetID <> :reset_id
                                  AND UsedAt IS NULL
                                  AND RevokedAt IS NULL;
                            """),
                            {
                                "user_id": user_id,
                                "reset_id":
                                    reset_record["PasswordResetID"],
                            },
                        )

                        password_updated = True

    except SQLAlchemyError as exc:
        logger.exception("Password reset database operation failed.")
        raise HTTPException(
            status_code=503,
            detail="Password reset is temporarily unavailable. Please retry.",
        ) from exc

    except Exception as exc:
        logger.exception("Password reset operation failed.")
        raise HTTPException(
            status_code=500,
            detail="Password reset could not be completed.",
        ) from exc

    if invalid_reset or not password_updated:
        raise HTTPException(
            status_code=400,
            detail="Invalid or expired password reset code.",
        )

    return {
        "message": (
            "Your password has been reset successfully. "
            "Please sign in with your new password."
        )
    }
