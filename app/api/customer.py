import logging

from fastapi import APIRouter, HTTPException, Query, Response, status
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from app.database import engine
from app.schemas.customer import (
    CustomerCreate,
    CustomerPatch,
    CustomerReplace,
    CustomerResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/v1/customers",
    tags=["Customers"],
)

CUSTOMER_COLUMNS = """
    CustomerID AS customer_id,
    FullName AS full_name,
    Phone AS phone,
    CreditLimit AS credit_limit,
    PaymentTerms AS payment_terms
"""


@router.get("", response_model=list[CustomerResponse])
def list_customers(
    search: str | None = Query(default=None, max_length=100),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
):
    query = f"""
        SELECT {CUSTOMER_COLUMNS}
        FROM dbo.Customers
        WHERE (
            :search IS NULL
            OR FullName LIKE :search_pattern
            OR Phone LIKE :search_pattern
        )
        ORDER BY CustomerID
        OFFSET :offset ROWS
        FETCH NEXT :limit ROWS ONLY;
    """

    try:
        with engine.connect() as connection:
            result = connection.execute(
                text(query),
                {
                    "search": search,
                    "search_pattern": f"%{search}%" if search else None,
                    "offset": offset,
                    "limit": limit,
                },
            )
            return [dict(row) for row in result.mappings()]

    except SQLAlchemyError:
        logger.exception("Failed to list customers.")
        raise HTTPException(
            status_code=503,
            detail="Customer information is temporarily unavailable.",
        )


@router.get("/{customer_id}", response_model=CustomerResponse)
def get_customer(customer_id: int):
    if customer_id <= 0:
        raise HTTPException(status_code=422, detail="Customer ID must be positive.")

    query = text(f"""
        SELECT {CUSTOMER_COLUMNS}
        FROM dbo.Customers
        WHERE CustomerID = :customer_id;
    """)

    try:
        with engine.connect() as connection:
            row = connection.execute(
                query,
                {"customer_id": customer_id},
            ).mappings().first()

        if row is None:
            raise HTTPException(status_code=404, detail="Customer not found.")

        return dict(row)

    except HTTPException:
        raise
    except SQLAlchemyError:
        logger.exception("Failed to retrieve customer.")
        raise HTTPException(
            status_code=503,
            detail="Customer information is temporarily unavailable.",
        )


@router.post(
    "",
    response_model=CustomerResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_customer(customer: CustomerCreate):
    query = text("""
        INSERT INTO dbo.Customers (
            FullName,
            Phone,
            CreditLimit,
            PaymentTerms
        )
        OUTPUT
            INSERTED.CustomerID AS customer_id,
            INSERTED.FullName AS full_name,
            INSERTED.Phone AS phone,
            INSERTED.CreditLimit AS credit_limit,
            INSERTED.PaymentTerms AS payment_terms
        VALUES (
            :full_name,
            :phone,
            :credit_limit,
            :payment_terms
        );
    """)

    try:
        with engine.begin() as connection:
            row = connection.execute(
                query,
                customer.model_dump(),
            ).mappings().one()

        return dict(row)

    except IntegrityError:
        logger.exception("Customer creation violated a database constraint.")
        raise HTTPException(
            status_code=409,
            detail="The customer could not be created because of a database constraint.",
        )
    except SQLAlchemyError:
        logger.exception("Failed to create customer.")
        raise HTTPException(
            status_code=503,
            detail="Customer creation is temporarily unavailable.",
        )


@router.put("/{customer_id}", response_model=CustomerResponse)
def replace_customer(customer_id: int, customer: CustomerReplace):
    if customer_id <= 0:
        raise HTTPException(status_code=422, detail="Customer ID must be positive.")

    query = text("""
        UPDATE dbo.Customers
        SET
            FullName = :full_name,
            Phone = :phone,
            CreditLimit = :credit_limit,
            PaymentTerms = :payment_terms
        OUTPUT
            INSERTED.CustomerID AS customer_id,
            INSERTED.FullName AS full_name,
            INSERTED.Phone AS phone,
            INSERTED.CreditLimit AS credit_limit,
            INSERTED.PaymentTerms AS payment_terms
        WHERE CustomerID = :customer_id;
    """)

    try:
        with engine.begin() as connection:
            row = connection.execute(
                query,
                {
                    **customer.model_dump(),
                    "customer_id": customer_id,
                },
            ).mappings().first()

        if row is None:
            raise HTTPException(status_code=404, detail="Customer not found.")

        return dict(row)

    except HTTPException:
        raise
    except IntegrityError:
        logger.exception("Customer replacement violated a database constraint.")
        raise HTTPException(status_code=409, detail="The update conflicts with a database constraint.")
    except SQLAlchemyError:
        logger.exception("Failed to replace customer.")
        raise HTTPException(status_code=503, detail="Customer update is temporarily unavailable.")


@router.patch("/{customer_id}", response_model=CustomerResponse)
def update_customer(customer_id: int, customer: CustomerPatch):
    if customer_id <= 0:
        raise HTTPException(status_code=422, detail="Customer ID must be positive.")

    changes = customer.model_dump(exclude_unset=True)

    if not changes:
        raise HTTPException(status_code=400, detail="Provide at least one field to update.")

    for field in ("full_name", "credit_limit", "payment_terms"):
        if field in changes and changes[field] is None:
            raise HTTPException(
                status_code=422,
                detail=f"{field} cannot be null.",
            )

    # These column names come from our fixed allowlist, not from SQL input.
    columns = {
        "full_name": "FullName",
        "phone": "Phone",
        "credit_limit": "CreditLimit",
        "payment_terms": "PaymentTerms",
    }

    assignments = ", ".join(
        f"{columns[field]} = :{field}"
        for field in changes
    )

    query = text(f"""
        UPDATE dbo.Customers
        SET {assignments}
        OUTPUT
            INSERTED.CustomerID AS customer_id,
            INSERTED.FullName AS full_name,
            INSERTED.Phone AS phone,
            INSERTED.CreditLimit AS credit_limit,
            INSERTED.PaymentTerms AS payment_terms
        WHERE CustomerID = :customer_id;
    """)

    try:
        with engine.begin() as connection:
            row = connection.execute(
                query,
                {**changes, "customer_id": customer_id},
            ).mappings().first()

        if row is None:
            raise HTTPException(status_code=404, detail="Customer not found.")

        return dict(row)

    except HTTPException:
        raise
    except IntegrityError:
        logger.exception("Customer update violated a database constraint.")
        raise HTTPException(status_code=409, detail="The update conflicts with a database constraint.")
    except SQLAlchemyError:
        logger.exception("Failed to update customer.")
        raise HTTPException(status_code=503, detail="Customer update is temporarily unavailable.")


@router.delete("/{customer_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_customer(customer_id: int):
    if customer_id <= 0:
        raise HTTPException(status_code=422, detail="Customer ID must be positive.")

    try:
        with engine.begin() as connection:
            result = connection.execute(
                text("""
                    DELETE FROM dbo.Customers
                    WHERE CustomerID = :customer_id;
                """),
                {"customer_id": customer_id},
            )

            if result.rowcount == 0:
                raise HTTPException(status_code=404, detail="Customer not found.")

    except HTTPException:
        raise
    except IntegrityError:
        logger.exception("Customer deletion was blocked by a database relationship.")
        raise HTTPException(
            status_code=409,
            detail="This customer has related records and cannot be deleted.",
        )
    except SQLAlchemyError:
        logger.exception("Failed to delete customer.")
        raise HTTPException(
            status_code=503,
            detail="Customer deletion is temporarily unavailable.",
        )

    return Response(status_code=status.HTTP_204_NO_CONTENT)
