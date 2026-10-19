import logging

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.database import engine
from app.schemas.tax_rates import (
    TaxRateCreate,
    TaxRateReplace,
    TaxRateUpdate,
)


logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/v1/tax-rates",
    tags=["Tax Rates"],
)


TAX_RATE_COLUMNS = """
    TaxRateID,
    TaxCode,
    Rate,
    EffectiveFrom,
    EffectiveTo,
    TaxType,
    AppliesTo,
    IsActive
"""


def serialize_tax_rate(row):
    return {
        "tax_rate_id": row["TaxRateID"],
        "tax_code": row["TaxCode"],
        "rate": str(row["Rate"]),
        "effective_from": row["EffectiveFrom"],
        "effective_to": row["EffectiveTo"],
        "tax_type": row["TaxType"],
        "applies_to": row["AppliesTo"],
        "is_active": row["IsActive"],
    }


def handle_database_error(exc, action):
    message = str(exc.orig if hasattr(exc, "orig") else exc).lower()

    if "already exists for this tax code" in message:
        raise HTTPException(
            status_code=409,
            detail=(
                "An active tax rate already exists for this tax code "
                "and overlapping effective dates."
            ),
        )

    if (
        "reference constraint" in message
        or "foreign key constraint" in message
    ):
        raise HTTPException(
            status_code=409,
            detail=(
                "This tax rate is referenced by other records "
                "and cannot be deleted or changed in this way."
            ),
        )

    logger.exception("Database operation failed: %s", action)

    raise HTTPException(
        status_code=503,
        detail=f"Unable to {action}.",
    )


INSERT_QUERY = text("""
    INSERT INTO dbo.TaxRates (
        TaxCode, Rate, EffectiveFrom, EffectiveTo, TaxType, AppliesTo, IsActive
    )
    VALUES (
        :tax_code, :rate, :effective_from, :effective_to, :tax_type, :applies_to, :is_active
    );
""")

SELECT_BY_CODE_DATE_QUERY = text(f"""
    SELECT {TAX_RATE_COLUMNS}
    FROM dbo.TaxRates
    WHERE TaxCode = :tax_code AND EffectiveFrom = :effective_from
    ORDER BY TaxRateID DESC;
""")


@router.post("", status_code=201)
def create_tax_rate(payload: TaxRateCreate):
    data = payload.model_dump()

    try:
        with engine.begin() as connection:
            connection.execute(INSERT_QUERY, data)
            row = connection.execute(
                SELECT_BY_CODE_DATE_QUERY,
                {"tax_code": data["tax_code"], "effective_from": data["effective_from"]},
            ).mappings().first()

        return {"tax_rate": serialize_tax_rate(row)}

    except SQLAlchemyError as exc:
        handle_database_error(exc, "create tax rate")


@router.get("")
def get_tax_rates(
    is_active: bool | None = Query(default=None),
    tax_type: str | None = Query(default=None, min_length=1),
    applies_to: str | None = Query(
        default=None,
        pattern="^(Purchase|Sale|Both)$",
    ),
):
    query = text("""
        SELECT
            TaxRateID,
            TaxCode,
            Rate,
            EffectiveFrom,
            EffectiveTo,
            TaxType,
            AppliesTo,
            IsActive
        FROM dbo.TaxRates
        WHERE
            (:is_active IS NULL OR IsActive = :is_active)
            AND
            (:tax_type IS NULL OR TaxType = :tax_type)
            AND
            (:applies_to IS NULL OR AppliesTo = :applies_to)
        ORDER BY
            TaxCode,
            EffectiveFrom DESC,
            TaxRateID DESC;
    """)

    try:
        with engine.connect() as connection:
            rows = connection.execute(
                query,
                {
                    "is_active": is_active,
                    "tax_type": tax_type,
                    "applies_to": applies_to,
                },
            ).mappings().all()

        tax_rates = [
            serialize_tax_rate(row)
            for row in rows
        ]

        return {
            "count": len(tax_rates),
            "tax_rates": tax_rates,
        }

    except SQLAlchemyError as exc:
        handle_database_error(exc, "retrieve tax rates")


@router.get("/{tax_rate_id}")
def get_tax_rate(tax_rate_id: int):
    if tax_rate_id <= 0:
        raise HTTPException(
            status_code=422,
            detail="Tax rate ID must be greater than zero.",
        )

    query = text(f"""
        SELECT {TAX_RATE_COLUMNS}
        FROM dbo.TaxRates
        WHERE TaxRateID = :tax_rate_id;
    """)

    try:
        with engine.connect() as connection:
            row = connection.execute(
                query,
                {"tax_rate_id": tax_rate_id},
            ).mappings().first()

        if row is None:
            raise HTTPException(
                status_code=404,
                detail="Tax rate not found.",
            )

        return {"tax_rate": serialize_tax_rate(row)}

    except SQLAlchemyError as exc:
        handle_database_error(exc, "retrieve tax rate")


@router.patch("/{tax_rate_id}")
def update_tax_rate(
    tax_rate_id: int,
    payload: TaxRateUpdate,
):
    if tax_rate_id <= 0:
        raise HTTPException(
            status_code=422,
            detail="Tax rate ID must be greater than zero.",
        )

    changes = payload.model_dump(exclude_unset=True)

    if not changes:
        raise HTTPException(
            status_code=400,
            detail="Provide at least one field to update.",
        )

    select_query = text(f"SELECT {TAX_RATE_COLUMNS} FROM dbo.TaxRates WHERE TaxRateID = :tax_rate_id;")

    update_query = text("""
        UPDATE dbo.TaxRates
        SET
            TaxCode = :tax_code,
            Rate = :rate,
            EffectiveFrom = :effective_from,
            EffectiveTo = :effective_to,
            TaxType = :tax_type,
            AppliesTo = :applies_to,
            IsActive = :is_active
        WHERE TaxRateID = :tax_rate_id;
    """)

    try:
        with engine.begin() as connection:
            current_row = connection.execute(
                select_query,
                {"tax_rate_id": tax_rate_id},
            ).mappings().first()

            if current_row is None:
                raise HTTPException(
                    status_code=404,
                    detail="Tax rate not found.",
                )

            current = serialize_tax_rate(current_row)

            # Validate the complete resulting record, not just
            # the individual fields supplied in the PATCH request.
            merged = TaxRateCreate.model_validate(
                {**current, **changes}
            )

            parameters = merged.model_dump()
            parameters["tax_rate_id"] = tax_rate_id

            connection.execute(update_query, parameters)
            updated_row = connection.execute(
                select_query,
                {"tax_rate_id": tax_rate_id},
            ).mappings().one()

        return {"tax_rate": serialize_tax_rate(updated_row)}

    except HTTPException:
        raise

    except SQLAlchemyError as exc:
        handle_database_error(exc, "update tax rate")


@router.put("/{tax_rate_id}")
def replace_tax_rate(
    tax_rate_id: int,
    payload: TaxRateReplace,
):
    if tax_rate_id <= 0:
        raise HTTPException(
            status_code=422,
            detail="Tax rate ID must be greater than zero.",
        )

    update_query = text("""
        UPDATE dbo.TaxRates
        SET
            TaxCode = :tax_code,
            Rate = :rate,
            EffectiveFrom = :effective_from,
            EffectiveTo = :effective_to,
            TaxType = :tax_type,
            AppliesTo = :applies_to,
            IsActive = :is_active
        WHERE TaxRateID = :tax_rate_id;
    """)

    select_query = text(f"SELECT {TAX_RATE_COLUMNS} FROM dbo.TaxRates WHERE TaxRateID = :tax_rate_id;")

    try:
        with engine.begin() as connection:
            params = {**payload.model_dump(), "tax_rate_id": tax_rate_id}
            result = connection.execute(update_query, params)

            if result.rowcount == 0:
                raise HTTPException(
                    status_code=404,
                    detail="Tax rate not found.",
                )

            row = connection.execute(
                select_query, {"tax_rate_id": tax_rate_id}
            ).mappings().one()

        return {"tax_rate": serialize_tax_rate(row)}

    except HTTPException:
        raise

    except SQLAlchemyError as exc:
        handle_database_error(exc, "replace tax rate")


@router.delete("/{tax_rate_id}")
def delete_tax_rate(tax_rate_id: int):
    if tax_rate_id <= 0:
        raise HTTPException(
            status_code=422,
            detail="Tax rate ID must be greater than zero.",
        )

    delete_query = text("""
        DELETE FROM dbo.TaxRates
        WHERE TaxRateID = :tax_rate_id;
    """)

    try:
        with engine.begin() as connection:
            result = connection.execute(
                delete_query,
                {"tax_rate_id": tax_rate_id},
            )

        if result.rowcount == 0:
            raise HTTPException(
                status_code=404,
                detail="Tax rate not found.",
            )

        return {
            "message": "Tax rate deleted successfully.",
            "tax_rate_id": tax_rate_id,
        }

    except HTTPException:
        raise

    except SQLAlchemyError as exc:
        handle_database_error(exc, "delete tax rate")