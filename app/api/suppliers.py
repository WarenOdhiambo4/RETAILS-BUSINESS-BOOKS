import logging

from fastapi import APIRouter, HTTPException, Query, Response, status
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from app.database import engine
from app.schemas.supplier import (
    SupplierCreate,
    SupplierReplace,
    SupplierPatch,
    SupplierResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/v1/suppliers",
    tags=["Suppliers"],
)

SUPPLIER_COLUMNS = """
    SupplierID AS supplier_id,
    FullName AS full_name,
    Contact AS contact,
    PaymentTerms AS payment_terms
"""


@router.get("", response_model=list[SupplierResponse])
def list_suppliers(
    search: str | None = Query(default=None, max_length=100),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
):
    query = text(f"""
        SELECT {SUPPLIER_COLUMNS}
        FROM dbo.Suppliers
        WHERE (
            :search IS NULL
            OR FullName LIKE :search_pattern
            OR Contact LIKE :search_pattern
        )
        ORDER BY SupplierID
        OFFSET :offset ROWS
        FETCH NEXT :limit ROWS ONLY;
    """)

    try:
        with engine.connect() as connection:
            result = connection.execute(
                query,
                {
                    "search": search,
                    "search_pattern": f"%{search}%" if search else None,
                    "offset": offset,
                    "limit": limit,
                },
            )
            return [dict(row) for row in result.mappings()]

    except SQLAlchemyError:
        logger.exception("Failed to list suppliers.")
        raise HTTPException(
            status_code=503,
            detail="Supplier information is temporarily unavailable.",
        )


@router.get("/{supplier_id}", response_model=SupplierResponse)
def get_supplier(supplier_id: int):
    if supplier_id <= 0:
        raise HTTPException(
            status_code=422,
            detail="Supplier ID must be positive.",
        )

    query = text(f"""
        SELECT {SUPPLIER_COLUMNS}
        FROM dbo.Suppliers
        WHERE SupplierID = :supplier_id;
    """)

    try:
        with engine.connect() as connection:
            row = connection.execute(
                query,
                {"supplier_id": supplier_id},
            ).mappings().first()

        if row is None:
            raise HTTPException(
                status_code=404,
                detail="Supplier not found.",
            )

        return dict(row)

    except HTTPException:
        raise
    except SQLAlchemyError:
        logger.exception("Failed to retrieve supplier.")
        raise HTTPException(
            status_code=503,
            detail="Supplier information is temporarily unavailable.",
        )


@router.post(
    "",
    response_model=SupplierResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_supplier(supplier: SupplierCreate):
    query = text("""
        INSERT INTO dbo.Suppliers (
            FullName,
            Contact,
            PaymentTerms
        )
        OUTPUT
            INSERTED.SupplierID AS supplier_id,
            INSERTED.FullName AS full_name,
            INSERTED.Contact AS contact,
            INSERTED.PaymentTerms AS payment_terms
        VALUES (
            :full_name,
            :contact,
            :payment_terms
        );
    """)

    try:
        with engine.begin() as connection:
            row = connection.execute(
                query,
                supplier.model_dump(),
            ).mappings().one()

        return dict(row)

    except IntegrityError:
        logger.exception("Supplier creation violated a database constraint.")
        raise HTTPException(
            status_code=409,
            detail="The supplier could not be created because of a database constraint.",
        )
    except SQLAlchemyError:
        logger.exception("Failed to create supplier.")
        raise HTTPException(
            status_code=503,
            detail="Supplier creation is temporarily unavailable.",
        )


@router.put("/{supplier_id}", response_model=SupplierResponse)
def replace_supplier(
    supplier_id: int,
    supplier: SupplierReplace,
):
    if supplier_id <= 0:
        raise HTTPException(
            status_code=422,
            detail="Supplier ID must be positive.",
        )

    query = text("""
        UPDATE dbo.Suppliers
        SET
            FullName = :full_name,
            Contact = :contact,
            PaymentTerms = :payment_terms
        OUTPUT
            INSERTED.SupplierID AS supplier_id,
            INSERTED.FullName AS full_name,
            INSERTED.Contact AS contact,
            INSERTED.PaymentTerms AS payment_terms
        WHERE SupplierID = :supplier_id;
    """)

    try:
        with engine.begin() as connection:
            row = connection.execute(
                query,
                {
                    **supplier.model_dump(),
                    "supplier_id": supplier_id,
                },
            ).mappings().first()

        if row is None:
            raise HTTPException(
                status_code=404,
                detail="Supplier not found.",
            )

        return dict(row)

    except HTTPException:
        raise
    except IntegrityError:
        logger.exception("Supplier replacement violated a database constraint.")
        raise HTTPException(
            status_code=409,
            detail="The update conflicts with a database constraint.",
        )
    except SQLAlchemyError:
        logger.exception("Failed to replace supplier.")
        raise HTTPException(
            status_code=503,
            detail="Supplier update is temporarily unavailable.",
        )


@router.patch("/{supplier_id}", response_model=SupplierResponse)
def update_supplier(
    supplier_id: int,
    supplier: SupplierPatch,
):
    if supplier_id <= 0:
        raise HTTPException(
            status_code=422,
            detail="Supplier ID must be positive.",
        )

    changes = supplier.model_dump(exclude_unset=True)

    if not changes:
        raise HTTPException(
            status_code=400,
            detail="Provide at least one field to update.",
        )

    for field in ("full_name", "payment_terms"):
        if field in changes and changes[field] is None:
            raise HTTPException(
                status_code=422,
                detail=f"{field} cannot be null.",
            )

    allowed_columns = {
        "full_name": "FullName",
        "contact": "Contact",
        "payment_terms": "PaymentTerms",
    }

    assignments = ", ".join(
        f"{allowed_columns[field]} = :{field}"
        for field in changes
    )

    query = text(f"""
        UPDATE dbo.Suppliers
        SET {assignments}
        OUTPUT
            INSERTED.SupplierID AS supplier_id,
            INSERTED.FullName AS full_name,
            INSERTED.Contact AS contact,
            INSERTED.PaymentTerms AS payment_terms
        WHERE SupplierID = :supplier_id;
    """)

    try:
        with engine.begin() as connection:
            row = connection.execute(
                query,
                {
                    **changes,
                    "supplier_id": supplier_id,
                },
            ).mappings().first()

        if row is None:
            raise HTTPException(
                status_code=404,
                detail="Supplier not found.",
            )

        return dict(row)

    except HTTPException:
        raise
    except IntegrityError:
        logger.exception("Supplier update violated a database constraint.")
        raise HTTPException(
            status_code=409,
            detail="The update conflicts with a database constraint.",
        )
    except SQLAlchemyError:
        logger.exception("Failed to update supplier.")
        raise HTTPException(
            status_code=503,
            detail="Supplier update is temporarily unavailable.",
        )


@router.delete(
    "/{supplier_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def delete_supplier(supplier_id: int):
    if supplier_id <= 0:
        raise HTTPException(
            status_code=422,
            detail="Supplier ID must be positive.",
        )

    try:
        with engine.begin() as connection:
            result = connection.execute(
                text("""
                    DELETE FROM dbo.Suppliers
                    WHERE SupplierID = :supplier_id;
                """),
                {"supplier_id": supplier_id},
            )

            if result.rowcount == 0:
                raise HTTPException(
                    status_code=404,
                    detail="Supplier not found.",
                )

    except HTTPException:
        raise
    except IntegrityError:
        logger.exception("Supplier deletion was blocked by related records.")
        raise HTTPException(
            status_code=409,
            detail=(
                "This supplier has related financial or purchasing records "
                "and cannot be deleted."
            ),
        )
    except SQLAlchemyError:
        logger.exception("Failed to delete supplier.")
        raise HTTPException(
            status_code=503,
            detail="Supplier deletion is temporarily unavailable.",
        )

    return Response(status_code=status.HTTP_204_NO_CONTENT)
