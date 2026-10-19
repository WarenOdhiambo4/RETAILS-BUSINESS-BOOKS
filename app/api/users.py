
import logging
from io import BytesIO

from fastapi import File, UploadFile, Response
from PIL import Image, UnidentifiedImageError
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from app.database import engine
from app.schemas.auth import ProfileUpdateRequest
from app.security_dependencies import get_current_user


logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/v1/users",
    tags=["Users"],
)


@router.get("/me")
def get_my_profile(
    current_user: dict = Depends(get_current_user),
):
    """Return the authenticated user's account and profile details."""

    try:
        with engine.connect() as connection:
            profile = connection.execute(
                text("""
                    SELECT
                        u.UserID,
                        u.UserGuid,
                        u.Email,
                        u.EmailVerified,
                        u.AccountStatus,
                        u.CreatedAt AS AccountCreatedAt,
                        u.LastLoginAt,
                        up.DisplayName,
                        up.MiddleName,
                        up.DateOfBirth,
                        up.ProfilePictureKey,
                        up.Bio,
                        up.WebsiteUrl,
                        up.AddressLine1,
                        up.AddressLine2,
                        up.City,
                        up.Region,
                        up.PostalCode,
                        up.CountryCode,
                        up.TimeZone,
                        up.PreferredLanguage,
                        up.CreatedAt AS ProfileCreatedAt,
                        up.UpdatedAt AS ProfileUpdatedAt
                    FROM dbo.Users AS u
                    INNER JOIN dbo.UserProfiles AS up
                        ON up.UserID = u.UserID
                    WHERE u.UserID = :user_id
                      AND u.EmailVerified = 1
                      AND u.AccountStatus = N'Active';
                """),
                {"user_id": current_user["UserID"]},
            ).mappings().first()

        if profile is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="User profile was not found.",
            )

        return dict(profile)

    except HTTPException:
        raise

    except SQLAlchemyError as exc:
        logger.exception("Profile retrieval failed.")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Your profile is temporarily unavailable.",
        ) from exc


@router.patch("/me")
def update_my_profile(
    payload: ProfileUpdateRequest,
    current_user: dict = Depends(get_current_user),
):
    """Update only personal profile fields supplied by the user."""

    changes = payload.model_dump(exclude_unset=True)

    if not changes:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Provide at least one field to update.",
        )

    # These columns are controlled by account/authentication workflows.
    # Only fields in this allowlist may be updated here.
    allowed_columns = {
        "display_name": "DisplayName",
        "middle_name": "MiddleName",
        "date_of_birth": "DateOfBirth",
        "bio": "Bio",
        "website_url": "WebsiteUrl",
        "address_line1": "AddressLine1",
        "address_line2": "AddressLine2",
        "city": "City",
        "region": "Region",
        "postal_code": "PostalCode",
        "country_code": "CountryCode",
        "time_zone": "TimeZone",
        "preferred_language": "PreferredLanguage",
    }

    # ProfileUpdateRequest currently contains the original fields
    # from the Users table. Reject unsupported fields until we update
    # the schema to match UserProfiles.
    unsupported_fields = set(changes) - set(allowed_columns)

    if unsupported_fields:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                "The profile schema must be updated before these fields "
                "can be edited: "
                + ", ".join(sorted(unsupported_fields))
            ),
        )

    assignments = [
        f"{allowed_columns[field]} = :{field}"
        for field in changes
    ]
    assignments.append("UpdatedAt = SYSUTCDATETIME()")

    parameters = {
        **changes,
        "user_id": current_user["UserID"],
    }

    try:
        with engine.begin() as connection:
            updated = connection.execute(
                text(f"""
                    UPDATE dbo.UserProfiles
                    SET {", ".join(assignments)}
                    WHERE UserID = :user_id
                      AND EXISTS (
                          SELECT 1
                          FROM dbo.Users AS u
                          WHERE u.UserID = dbo.UserProfiles.UserID
                            AND u.EmailVerified = 1
                            AND u.AccountStatus = N'Active'
                      );
                """),
                parameters,
            )

            if updated.rowcount != 1:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="The profile cannot be updated in the current account state.",
                )

            profile = connection.execute(
                text("""
                    SELECT *
                    FROM dbo.UserProfiles
                    WHERE UserID = :user_id;
                """),
                {"user_id": current_user["UserID"]},
            ).mappings().one()

            return dict(profile)

    except HTTPException:
        raise

    except IntegrityError as exc:
        logger.warning(
            "Profile constraint violation for user_id=%s.",
            current_user["UserID"],
        )
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="One or more profile values violate a database constraint.",
        ) from exc

    except SQLAlchemyError as exc:
        logger.exception("Profile update failed.")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Your profile could not be updated right now.",
        ) from exc

MAX_PROFILE_PICTURE_BYTES = 5 * 1024 * 1024

ALLOWED_IMAGE_FORMATS = {
    "JPEG": "image/jpeg",
    "PNG": "image/png",
    "WEBP": "image/webp",
}


@router.put("/me/profile-picture")
def upload_profile_picture(
    file: UploadFile = File(...),
    current_user: dict = Depends(get_current_user),
):
    """Upload or replace the authenticated user's profile picture."""

    image_data = None

    try:
        # Read at most one byte beyond the limit so oversized uploads
        # can be rejected without loading the entire file into memory.
        
        image_data = file.file.read(MAX_PROFILE_PICTURE_BYTES + 1)

        

        if not image_data:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="The uploaded image is empty.",
            )

        if len(image_data) > MAX_PROFILE_PICTURE_BYTES:
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail="The image must not exceed 5 MB.",
            )

        try:
            with Image.open(BytesIO(image_data)) as image:
                image_format = image.format

                if image_format not in ALLOWED_IMAGE_FORMATS:
                    raise HTTPException(
                        status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
                        detail="Only JPEG, PNG, and WebP images are allowed.",
                    )

                image.verify()

        except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="The uploaded file is not a valid supported image.",
            ) from exc

        content_type = ALLOWED_IMAGE_FORMATS[image_format]

        with engine.begin() as connection:
            updated = connection.execute(
                text("""
                    UPDATE dbo.UserProfiles
                    SET
                        ProfilePictureData = :image_data,
                        ProfilePictureContentType = :content_type,
                        ProfilePictureUpdatedAt = SYSUTCDATETIME(),
                        UpdatedAt = SYSUTCDATETIME()
                    WHERE UserID = :user_id
                      AND EXISTS (
                          SELECT 1
                          FROM dbo.Users AS u
                          WHERE u.UserID = dbo.UserProfiles.UserID
                            AND u.EmailVerified = 1
                            AND u.AccountStatus = N'Active'
                      );
                """),
                {
                    "image_data": image_data,
                    "content_type": content_type,
                    "user_id": current_user["UserID"],
                },
            )

            if updated.rowcount != 1:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="The profile cannot be updated in the current account state.",
                )

        return {
            "message": "Profile picture uploaded successfully.",
            "content_type": content_type,
            "size_bytes": len(image_data),
            "picture_url": "/api/v1/users/me/profile-picture",
        }

    except HTTPException:
        raise

    except SQLAlchemyError as exc:
        logger.exception("Profile picture upload failed.")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="The profile picture could not be saved.",
        ) from exc

    finally:
        # Close the incoming upload even if validation or persistence fails.
        # The upload's bytes have already been read into memory.
        pass



@router.get("/me/profile-picture")
def get_profile_picture(
    current_user: dict = Depends(get_current_user),
):
    """Return the authenticated user's profile picture."""

    try:
        with engine.connect() as connection:
            picture = connection.execute(
                text("""
                    SELECT
                        up.ProfilePictureData,
                        up.ProfilePictureContentType
                    FROM dbo.UserProfiles AS up
                    INNER JOIN dbo.Users AS u
                        ON u.UserID = up.UserID
                    WHERE up.UserID = :user_id
                      AND u.EmailVerified = 1
                      AND u.AccountStatus = N'Active';
                """),
                {"user_id": current_user["UserID"]},
            ).mappings().first()

        if picture is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="The user profile was not found.",
            )

        if picture["ProfilePictureData"] is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="No profile picture has been uploaded.",
            )

        return Response(
            content=bytes(picture["ProfilePictureData"]),
            media_type=picture["ProfilePictureContentType"],
            headers={"Cache-Control": "private, no-store"},
        )

    except HTTPException:
        raise

    except SQLAlchemyError as exc:
        logger.exception("Profile picture retrieval failed.")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="The profile picture is temporarily unavailable.",
        ) from exc




@router.delete("/me/profile-picture")
def delete_profile_picture(
    current_user: dict = Depends(get_current_user),
):
    """Remove the authenticated user's profile picture."""

    try:
        with engine.begin() as connection:
            updated = connection.execute(
                text("""
                    UPDATE dbo.UserProfiles
                    SET
                        ProfilePictureData = NULL,
                        ProfilePictureContentType = NULL,
                        ProfilePictureUpdatedAt = NULL,
                        UpdatedAt = SYSUTCDATETIME()
                    WHERE UserID = :user_id
                      AND EXISTS (
                          SELECT 1
                          FROM dbo.Users AS u
                          WHERE u.UserID = dbo.UserProfiles.UserID
                            AND u.EmailVerified = 1
                            AND u.AccountStatus = N'Active'
                      );
                """),
                {"user_id": current_user["UserID"]},
            )

            if updated.rowcount != 1:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="The profile cannot be updated in the current account state.",
                )

        return {"message": "Profile picture removed successfully."}

    except HTTPException:
        raise

    except SQLAlchemyError as exc:
        logger.exception("Profile picture removal failed.")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="The profile picture could not be removed.",
        ) from exc
