import logging

from fastapi import APIRouter, HTTPException, Query, Response, status
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from app.database import engine
from app.schemas.product import (
    ProductCreate,
    ProductReplace,
    ProductPatch,
    ProductResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/v1/products",
    tags=["Products"],
)

PRODUCT_COLUMNS = """
    ProductID AS product_id,
    UPC AS upc,
    ProductName AS product_name,
    CategoryID AS category_id,
    Unit AS unit,
    Barcode AS barcode,
    Active AS active
"""


@router.get("", response_model=list[ProductResponse])
def list_products(
    search: str | None = Query(default=None, max_length=100),
    category_id: int | None = Query(default=None, gt=0),
    active: bool | None = None,
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
):
    query = text(f"""
        SELECT {PRODUCT_COLUMNS}
        FROM dbo.Products
        WHERE (
            :search IS NULL
            OR ProductName LIKE :search_pattern
            OR UPC LIKE :search_pattern
            OR Barcode LIKE :search_pattern
        )
        AND (:category_id IS NULL OR CategoryID = :category_id)
        AND (:active IS NULL OR Active = :active)
        ORDER BY ProductID
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
                    "category_id": category_id,
                    "active": active,
                    "offset": offset,
                    "limit": limit,
                },
            )
            return [dict(row) for row in result.mappings()]

    except SQLAlchemyError:
        logger.exception("Failed to list products.")
        raise HTTPException(
            status_code=503,
            detail="Product information is temporarily unavailable.",
        )


@router.get("/{product_id}", response_model=ProductResponse)
def get_product(product_id: int):
    if product_id <= 0:
        raise HTTPException(status_code=422, detail="Product ID must be positive.")

    try:
        with engine.connect() as connection:
            row = connection.execute(
                text(f"""
                    SELECT {PRODUCT_COLUMNS}
                    FROM dbo.Products
                    WHERE ProductID = :product_id;
                """),
                {"product_id": product_id},
            ).mappings().first()

        if row is None:
            raise HTTPException(status_code=404, detail="Product not found.")

        return dict(row)

    except HTTPException:
        raise
    except SQLAlchemyError:
        logger.exception("Failed to retrieve product.")
        raise HTTPException(
            status_code=503,
            detail="Product information is temporarily unavailable.",
        )


@router.post(
    "",
    response_model=ProductResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_product(product: ProductCreate):
    query = text("""
        INSERT INTO dbo.Products (
            UPC,
            ProductName,
            CategoryID,
            Unit,
            Barcode,
            Active
        )
        OUTPUT
            INSERTED.ProductID AS product_id,
            INSERTED.UPC AS upc,
            INSERTED.ProductName AS product_name,
            INSERTED.CategoryID AS category_id,
            INSERTED.Unit AS unit,
            INSERTED.Barcode AS barcode,
            INSERTED.Active AS active
        VALUES (
            :upc,
            :product_name,
            :category_id,
            :unit,
            :barcode,
            :active
        );
    """)

    try:
        with engine.begin() as connection:
            row = connection.execute(
                query,
                product.model_dump(),
            ).mappings().one()

        return dict(row)

    except IntegrityError:
        logger.exception("Product creation violated a database constraint.")
        raise HTTPException(
            status_code=409,
            detail=(
                "The product conflicts with a database constraint. "
                "Check the category and any uniqueness rules."
            ),
        )
    except SQLAlchemyError:
        logger.exception("Failed to create product.")
        raise HTTPException(
            status_code=503,
            detail="Product creation is temporarily unavailable.",
        )


@router.put("/{product_id}", response_model=ProductResponse)
def replace_product(product_id: int, product: ProductReplace):
    if product_id <= 0:
        raise HTTPException(status_code=422, detail="Product ID must be positive.")

    query = text("""
        UPDATE dbo.Products
        SET
            UPC = :upc,
            ProductName = :product_name,
            CategoryID = :category_id,
            Unit = :unit,
            Barcode = :barcode,
            Active = :active
        OUTPUT
            INSERTED.ProductID AS product_id,
            INSERTED.UPC AS upc,
            INSERTED.ProductName AS product_name,
            INSERTED.CategoryID AS category_id,
            INSERTED.Unit AS unit,
            INSERTED.Barcode AS barcode,
            INSERTED.Active AS active
        WHERE ProductID = :product_id;
    """)

    try:
        with engine.begin() as connection:
            row = connection.execute(
                query,
                {
                    **product.model_dump(),
                    "product_id": product_id,
                },
            ).mappings().first()

        if row is None:
            raise HTTPException(status_code=404, detail="Product not found.")

        return dict(row)

    except HTTPException:
        raise
    except IntegrityError:
        logger.exception("Product replacement violated a database constraint.")
        raise HTTPException(
            status_code=409,
            detail="The update conflicts with a database constraint.",
        )
    except SQLAlchemyError:
        logger.exception("Failed to replace product.")
        raise HTTPException(
            status_code=503,
            detail="Product update is temporarily unavailable.",
        )


@router.patch("/{product_id}", response_model=ProductResponse)
def update_product(product_id: int, product: ProductPatch):
    if product_id <= 0:
        raise HTTPException(status_code=422, detail="Product ID must be positive.")

    changes = product.model_dump(exclude_unset=True)

    if not changes:
        raise HTTPException(
            status_code=400,
            detail="Provide at least one field to update.",
        )

    for field in ("upc", "product_name", "category_id", "unit"):
        if field in changes and changes[field] is None:
            raise HTTPException(
                status_code=422,
                detail=f"{field} cannot be null.",
            )

    allowed_columns = {
        "upc": "UPC",
        "product_name": "ProductName",
        "category_id": "CategoryID",
        "unit": "Unit",
        "barcode": "Barcode",
        "active": "Active",
    }

    assignments = ", ".join(
        f"{allowed_columns[field]} = :{field}"
        for field in changes
    )

    query = text(f"""
        UPDATE dbo.Products
        SET {assignments}
        OUTPUT
            INSERTED.ProductID AS product_id,
            INSERTED.UPC AS upc,
            INSERTED.ProductName AS product_name,
            INSERTED.CategoryID AS category_id,
            INSERTED.Unit AS unit,
            INSERTED.Barcode AS barcode,
            INSERTED.Active AS active
        WHERE ProductID = :product_id;
    """)

    try:
        with engine.begin() as connection:
            row = connection.execute(
                query,
                {**changes, "product_id": product_id},
            ).mappings().first()

        if row is None:
            raise HTTPException(status_code=404, detail="Product not found.")

        return dict(row)

    except HTTPException:
        raise
    except IntegrityError:
        logger.exception("Product update violated a database constraint.")
        raise HTTPException(
            status_code=409,
            detail="The update conflicts with a database constraint.",
        )
    except SQLAlchemyError:
        logger.exception("Failed to update product.")
        raise HTTPException(
            status_code=503,
            detail="Product update is temporarily unavailable.",
        )


@router.delete(
    "/{product_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def delete_product(product_id: int):
    if product_id <= 0:
        raise HTTPException(status_code=422, detail="Product ID must be positive.")

    try:
        with engine.begin() as connection:
            result = connection.execute(
                text("""
                    DELETE FROM dbo.Products
                    WHERE ProductID = :product_id;
                """),
                {"product_id": product_id},
            )

            if result.rowcount == 0:
                raise HTTPException(status_code=404, detail="Product not found.")

    except HTTPException:
        raise
    except IntegrityError:
        logger.exception("Product deletion was blocked by related records.")
        raise HTTPException(
            status_code=409,
            detail=(
                "This product has related purchasing, sales, or inventory records "
                "and cannot be deleted."
            ),
        )
    except SQLAlchemyError:
        logger.exception("Failed to delete product.")
        raise HTTPException(
            status_code=503,
            detail="Product deletion is temporarily unavailable.",
        )

    return Response(status_code=status.HTTP_204_NO_CONTENT)
