
import logging

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Query,
    Response,
    status,
)
from io import BytesIO

from fastapi import File, UploadFile
from PIL import Image, UnidentifiedImageError
from app.security_dependencies import get_current_user, require_admin
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from app.database import engine
from app.schemas.warehouse import (
    WarehouseCreate,
    WarehouseReplace,
    WarehousePatch,
    WarehouseResponse,
)


logger = logging.getLogger(__name__)
MAX_WAREHOUSE_IMAGE_BYTES = 5 * 1024 * 1024

ALLOWED_IMAGE_FORMATS = {
    "JPEG": "image/jpeg",
    "PNG": "image/png",
    "WEBP": "image/webp",
}
router = APIRouter(
    prefix="/api/v1/warehouses",
    tags=["Warehouses"],
)


WAREHOUSE_COLUMNS = """
    w.WarehouseID AS warehouse_id,
    w.WarehouseName AS warehouse_name,
    w.WarehouseLocation AS warehouse_location,
    w.WarehouseCode AS warehouse_code,
    w.Description AS description,
    w.Status AS status,
    w.AddressLine1 AS address_line1,
    w.AddressLine2 AS address_line2,
    w.City AS city,
    w.Region AS region,
    w.CountryCode AS country_code,
    w.ContactPhone AS contact_phone,
    w.ContactEmail AS contact_email,
    w.CreatedAt AS created_at,
    w.UpdatedAt AS updated_at,
    w.CreatedByUserID AS created_by_user_id,
    w.UpdatedByUserID AS updated_by_user_id
"""


WAREHOUSE_WRITE_COLUMNS = {
    "warehouse_name": "WarehouseName",
    "warehouse_location": "WarehouseLocation",
    "warehouse_code": "WarehouseCode",
    "description": "Description",
    "status": "Status",
    "address_line1": "AddressLine1",
    "address_line2": "AddressLine2",
    "city": "City",
    "region": "Region",
    "country_code": "CountryCode",
    "contact_phone": "ContactPhone",
    "contact_email": "ContactEmail",
}


def normalize_warehouse_fields(changes: dict) -> dict:
    """Normalize user-supplied text without changing unspecified fields."""
    changes = changes.copy()

    if "warehouse_name" in changes and changes["warehouse_name"] is not None:
        changes["warehouse_name"] = changes["warehouse_name"].strip()

        if not changes["warehouse_name"]:
            raise HTTPException(
                status_code=422,
                detail="Warehouse name cannot be blank.",
            )

    if "warehouse_code" in changes and changes["warehouse_code"] is not None:
        changes["warehouse_code"] = changes["warehouse_code"].strip() or None

    if "status" in changes and changes["status"] is not None:
        changes["status"] = changes["status"].strip()

        if not changes["status"]:
            raise HTTPException(
                status_code=422,
                detail="Warehouse status cannot be blank.",
            )

    if "country_code" in changes and changes["country_code"] is not None:
        changes["country_code"] = changes["country_code"].strip().upper()

    return changes


def get_warehouse_row(connection, warehouse_id: int):
    """Read the full profile for one warehouse."""
    return connection.execute(
        text(f"""
            SELECT {WAREHOUSE_COLUMNS}
            FROM dbo.Warehouses AS w
            WHERE w.WarehouseID = :warehouse_id
        """),
        {"warehouse_id": warehouse_id},
    ).mappings().first()

@router.get("", response_model=list[WarehouseResponse])
def list_warehouses(
    search: str | None = Query(default=None, max_length=100),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    current_user: dict = Depends(get_current_user),
):
    sql = f"""
        SELECT {WAREHOUSE_COLUMNS}
        FROM dbo.Warehouses AS w
        WHERE (
            EXISTS (
                SELECT 1
                FROM dbo.UserRoles AS ur
                INNER JOIN dbo.Roles AS r
                    ON r.RoleID = ur.RoleID
                WHERE ur.UserID = :user_id
                  AND r.RoleName = N'Admin'
            )
            OR EXISTS (
                SELECT 1
                FROM dbo.WarehouseUserAssignments AS a
                WHERE a.WarehouseID = w.WarehouseID
                  AND a.UserID = :user_id
                  AND a.IsActive = 1
            )
        )
        AND (
            :search IS NULL
            OR w.WarehouseName LIKE N'%' + :search + N'%'
            OR w.WarehouseLocation LIKE N'%' + :search + N'%'
            OR w.WarehouseCode LIKE N'%' + :search + N'%'
            OR w.City LIKE N'%' + :search + N'%'
            OR w.Region LIKE N'%' + :search + N'%'
        )
        ORDER BY w.WarehouseID
        OFFSET :offset ROWS
        FETCH NEXT :limit ROWS ONLY
    """

    try:
        with engine.connect() as connection:
            rows = connection.execute(
                text(sql),
                {
                    "user_id": current_user["UserID"],
                    "search": search,
                    "limit": limit,
                    "offset": offset,
                },
            ).mappings().all()

        return [dict(row) for row in rows]

    except SQLAlchemyError:
        logger.exception("Warehouse listing failed.")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Unable to retrieve warehouses.",
        )


@router.get("/{warehouse_id}", response_model=WarehouseResponse)
def get_warehouse(
    warehouse_id: int,
    current_user: dict = Depends(get_current_user),
):
    if warehouse_id <= 0:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Warehouse ID must be greater than zero.",
        )

    sql = f"""
        SELECT {WAREHOUSE_COLUMNS}
        FROM dbo.Warehouses AS w
        WHERE w.WarehouseID = :warehouse_id
          AND (
              EXISTS (
                  SELECT 1
                  FROM dbo.UserRoles AS ur
                  INNER JOIN dbo.Roles AS r
                      ON r.RoleID = ur.RoleID
                  WHERE ur.UserID = :user_id
                    AND r.RoleName = N'Admin'
              )
              OR EXISTS (
                  SELECT 1
                  FROM dbo.WarehouseUserAssignments AS a
                  WHERE a.WarehouseID = w.WarehouseID
                    AND a.UserID = :user_id
                    AND a.IsActive = 1
              )
          )
    """

    try:
        with engine.connect() as connection:
            row = connection.execute(
                text(sql),
                {
                    "warehouse_id": warehouse_id,
                    "user_id": current_user["UserID"],
                },
            ).mappings().first()

        if row is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Warehouse not found.",
            )

        return dict(row)

    except HTTPException:
        raise
    except SQLAlchemyError:
        logger.exception("Warehouse retrieval failed.")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Unable to retrieve the warehouse.",
        )


@router.post(
    "",
    response_model=WarehouseResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_warehouse(payload: WarehouseCreate,):
    changes = normalize_warehouse_fields(
        payload.model_dump()
    )

    sql = """
        INSERT INTO dbo.Warehouses (
            WarehouseName,
            WarehouseLocation,
            WarehouseCode,
            Description,
            Status,
            AddressLine1,
            AddressLine2,
            City,
            Region,
            CountryCode,
            ContactPhone,
            ContactEmail
        )
        OUTPUT INSERTED.WarehouseID AS warehouse_id
        VALUES (
            :warehouse_name,
            :warehouse_location,
            :warehouse_code,
            :description,
            :status,
            :address_line1,
            :address_line2,
            :city,
            :region,
            :country_code,
            :contact_phone,
            :contact_email
        )
    """

    try:
        with engine.begin() as connection:
            result = connection.execute(
                text(sql),
                changes,
            ).mappings().one()

            row = get_warehouse_row(
                connection,
                result["warehouse_id"],
            )

        return dict(row)

    except IntegrityError:
        logger.exception("Warehouse creation violated a constraint.")
        raise HTTPException(
            status_code=409,
            detail="The warehouse conflicts with a database constraint.",
        )
    except SQLAlchemyError:
        logger.exception("Warehouse creation failed.")
        raise HTTPException(
            status_code=503,
            detail="Unable to create the warehouse.",
        )


@router.put("/{warehouse_id}", response_model=WarehouseResponse)
def replace_warehouse(
    warehouse_id: int,
    payload: WarehouseReplace,
):
    if warehouse_id <= 0:
        raise HTTPException(
            status_code=422,
            detail="Warehouse ID must be greater than zero.",
        )

    changes = normalize_warehouse_fields(
        payload.model_dump()
    )

    assignments = ", ".join(
        f"{column} = :{field}"
        for field, column in WAREHOUSE_WRITE_COLUMNS.items()
        if field in changes
    )

    sql = f"""
        UPDATE dbo.Warehouses
        SET
            {assignments},
            UpdatedAt = SYSUTCDATETIME()
        WHERE WarehouseID = :warehouse_id
    """

    try:
        with engine.begin() as connection:
            result = connection.execute(
                text(sql),
                {
                    **changes,
                    "warehouse_id": warehouse_id,
                },
            )

            if result.rowcount == 0:
                raise HTTPException(
                    status_code=404,
                    detail="Warehouse not found.",
                )

            row = get_warehouse_row(connection, warehouse_id)

        return dict(row)

    except HTTPException:
        raise
    except IntegrityError:
        logger.exception("Warehouse replacement violated a constraint.")
        raise HTTPException(
            status_code=409,
            detail="The update conflicts with a database constraint.",
        )
    except SQLAlchemyError:
        logger.exception("Warehouse replacement failed.")
        raise HTTPException(
            status_code=503,
            detail="Unable to update the warehouse.",
        )


@router.patch("/{warehouse_id}", response_model=WarehouseResponse)
def update_warehouse_fields(
    warehouse_id: int,
    payload: WarehousePatch,
):
    if warehouse_id <= 0:
        raise HTTPException(
            status_code=422,
            detail="Warehouse ID must be greater than zero.",
        )

    changes = payload.model_dump(exclude_unset=True)

    if not changes:
        raise HTTPException(
            status_code=400,
            detail="Provide at least one field to update.",
        )

    changes = normalize_warehouse_fields(changes)

    assignments = ", ".join(
        f"{WAREHOUSE_WRITE_COLUMNS[field]} = :{field}"
        for field in changes
    )

    sql = f"""
        UPDATE dbo.Warehouses
        SET
            {assignments},
            UpdatedAt = SYSUTCDATETIME()
        WHERE WarehouseID = :warehouse_id
    """

    try:
        with engine.begin() as connection:
            result = connection.execute(
                text(sql),
                {
                    **changes,
                    "warehouse_id": warehouse_id,
                },
            )

            if result.rowcount == 0:
                raise HTTPException(
                    status_code=404,
                    detail="Warehouse not found.",
                )

            row = get_warehouse_row(connection, warehouse_id)

        return dict(row)

    except HTTPException:
        raise
    except IntegrityError:
        logger.exception("Warehouse update violated a constraint.")
        raise HTTPException(
            status_code=409,
            detail="The update conflicts with a database constraint.",
        )
    except SQLAlchemyError:
        logger.exception("Warehouse update failed.")
        raise HTTPException(
            status_code=503,
            detail="Unable to update the warehouse.",
        )


@router.delete(
    "/{warehouse_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def delete_warehouse(warehouse_id: int):
    if warehouse_id <= 0:
        raise HTTPException(
            status_code=422,
            detail="Warehouse ID must be greater than zero.",
        )

    try:
        with engine.begin() as connection:
            result = connection.execute(
                text("""
                    DELETE FROM dbo.Warehouses
                    WHERE WarehouseID = :warehouse_id
                """),
                {"warehouse_id": warehouse_id},
            )

            if result.rowcount == 0:
                raise HTTPException(
                    status_code=404,
                    detail="Warehouse not found.",
                )

        return Response(status_code=status.HTTP_204_NO_CONTENT)

    except HTTPException:
        raise
    except IntegrityError:
        raise HTTPException(
            status_code=409,
            detail=(
                "This warehouse is referenced by existing records. "
                "It cannot be deleted."
            ),
        )
    except SQLAlchemyError:
        logger.exception("Warehouse deletion failed.")
        raise HTTPException(
            status_code=503,
            detail="Unable to delete the warehouse.",
        )

@router.patch("/{warehouse_id}/image")
@router.put("/{warehouse_id}/image")
def upload_warehouse_image(
    warehouse_id: int,
    file: UploadFile = File(...),
    current_user: dict = Depends(get_current_user),
):
    """Upload or replace a warehouse profile image."""

    if warehouse_id <= 0:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Warehouse ID must be greater than zero.",
        )

    image_data = None

    try:
        image_data = file.file.read(MAX_WAREHOUSE_IMAGE_BYTES + 1)

        if not image_data:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="The uploaded image is empty.",
            )

        if len(image_data) > MAX_WAREHOUSE_IMAGE_BYTES:
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

        except (
            UnidentifiedImageError,
            OSError,
            Image.DecompressionBombError,
        ) as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="The uploaded file is not a valid supported image.",
            ) from exc

        content_type = ALLOWED_IMAGE_FORMATS[image_format]
        user_id = current_user["UserID"]

        with engine.begin() as connection:
            authorized = connection.execute(
                text("""
                    SELECT w.WarehouseID
                    FROM dbo.Warehouses AS w
                    WHERE w.WarehouseID = :warehouse_id
                      AND (
                          EXISTS (
                              SELECT 1
                              FROM dbo.UserRoles AS ur
                              INNER JOIN dbo.Roles AS r
                                  ON r.RoleID = ur.RoleID
                              WHERE ur.UserID = :user_id
                                AND r.RoleName = N'Admin'
                          )
                          OR EXISTS (
                              SELECT 1
                              FROM dbo.WarehouseUserAssignments AS a
                              WHERE a.WarehouseID = w.WarehouseID
                                AND a.UserID = :user_id
                                AND a.AssignmentRole = N'Manager'
                                AND a.IsActive = 1
                          )
                      );
                """),
                {
                    "warehouse_id": warehouse_id,
                    "user_id": user_id,
                },
            ).first()

            if authorized is None:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Warehouse not found or you cannot manage its image.",
                )

            connection.execute(
                text("""
                    UPDATE dbo.Warehouses
                    SET
                        WarehouseImageData = :image_data,
                        WarehouseImageContentType = :content_type,
                        WarehouseImageUpdatedAt = SYSUTCDATETIME(),
                        UpdatedAt = SYSUTCDATETIME(),
                        UpdatedByUserID = :user_id
                    WHERE WarehouseID = :warehouse_id;
                """),
                {
                    "image_data": image_data,
                    "content_type": content_type,
                    "user_id": user_id,
                    "warehouse_id": warehouse_id,
                },
            )

        return {
            "message": "Warehouse image uploaded successfully.",
            "warehouse_id": warehouse_id,
            "content_type": content_type,
            "size_bytes": len(image_data),
            "image_url": f"/api/v1/warehouses/{warehouse_id}/image",
        }

    except HTTPException:
        raise

    except SQLAlchemyError as exc:
        logger.exception(
            "Warehouse image upload failed for warehouse_id=%s.",
            warehouse_id,
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="The warehouse image could not be saved.",
        ) from exc

    finally:
        file.file.close()



@router.get("/{warehouse_id}/image")
def get_warehouse_image(
    warehouse_id: int,
    current_user: dict = Depends(get_current_user),
):
    """Return the warehouse image for an authorized user."""

    if warehouse_id <= 0:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Warehouse ID must be greater than zero.",
        )

    try:
        with engine.connect() as connection:
            image = connection.execute(
                text("""
                    SELECT
                        w.WarehouseImageData,
                        w.WarehouseImageContentType
                    FROM dbo.Warehouses AS w
                    WHERE w.WarehouseID = :warehouse_id
                      AND (
                          EXISTS (
                              SELECT 1
                              FROM dbo.UserRoles AS ur
                              INNER JOIN dbo.Roles AS r
                                  ON r.RoleID = ur.RoleID
                              WHERE ur.UserID = :user_id
                                AND r.RoleName = N'Admin'
                          )
                          OR EXISTS (
                              SELECT 1
                              FROM dbo.WarehouseUserAssignments AS a
                              WHERE a.WarehouseID = w.WarehouseID
                                AND a.UserID = :user_id
                                AND a.IsActive = 1
                          )
                      );
                """),
                {
                    "warehouse_id": warehouse_id,
                    "user_id": current_user["UserID"],
                },
            ).mappings().first()

        if image is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Warehouse not found or access denied.",
            )

        if image["WarehouseImageData"] is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="No image has been uploaded for this warehouse.",
            )

        return Response(
            content=bytes(image["WarehouseImageData"]),
            media_type=image["WarehouseImageContentType"],
            headers={
                "Cache-Control": "private, no-store",
                "X-Content-Type-Options": "nosniff",
            },
        )

    except HTTPException:
        raise

    except SQLAlchemyError as exc:
        logger.exception(
            "Warehouse image retrieval failed for warehouse_id=%s.",
            warehouse_id,
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="The warehouse image is temporarily unavailable.",
        ) from exc



@router.delete("/{warehouse_id}/image")
def delete_warehouse_image(
    warehouse_id: int,
    current_user: dict = Depends(get_current_user),
):
    """Remove a warehouse image without deleting the warehouse."""

    if warehouse_id <= 0:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Warehouse ID must be greater than zero.",
        )

    try:
        with engine.begin() as connection:
            deleted = connection.execute(
                text("""
                    UPDATE dbo.Warehouses
                    SET
                        WarehouseImageData = NULL,
                        WarehouseImageContentType = NULL,
                        WarehouseImageUpdatedAt = NULL,
                        UpdatedAt = SYSUTCDATETIME(),
                        UpdatedByUserID = :user_id
                    WHERE WarehouseID = :warehouse_id
                      AND (
                          EXISTS (
                              SELECT 1
                              FROM dbo.UserRoles AS ur
                              INNER JOIN dbo.Roles AS r
                                  ON r.RoleID = ur.RoleID
                              WHERE ur.UserID = :user_id
                                AND r.RoleName = N'Admin'
                          )
                          OR EXISTS (
                              SELECT 1
                              FROM dbo.WarehouseUserAssignments AS a
                              WHERE a.WarehouseID = dbo.Warehouses.WarehouseID
                                AND a.UserID = :user_id
                                AND a.AssignmentRole = N'Manager'
                                AND a.IsActive = 1
                          )
                      );
                """),
                {
                    "warehouse_id": warehouse_id,
                    "user_id": current_user["UserID"],
                },
            )

            if deleted.rowcount != 1:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Warehouse not found or you cannot manage its image.",
                )

        return {
            "message": "Warehouse image removed successfully.",
            "warehouse_id": warehouse_id,
        }

    except HTTPException:
        raise

    except SQLAlchemyError as exc:
        logger.exception(
            "Warehouse image deletion failed for warehouse_id=%s.",
            warehouse_id,
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="The warehouse image could not be removed.",
        ) from exc
