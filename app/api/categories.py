
from fastapi import APIRouter, HTTPException, Query, Response, status
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from app.database import engine
from app.schemas.category import (
    CategoryCreate,
    CategoryReplace,
    CategoryPatch,
    CategoryResponse,
)

router = APIRouter(
    prefix="/api/v1/categories",
    tags=["Categories"],
)

CATEGORY_COLUMNS = """
    CategoryID AS category_id,
    CategoryName AS category_name
"""


def validate_category_name(name: str) -> str:
    name = name.strip()

    if not name:
        raise HTTPException(
            status_code=422,
            detail="Category name cannot be blank.",
        )

    return name


@router.get("", response_model=list[CategoryResponse])
def list_categories(
    search: str | None = Query(default=None, max_length=100),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
):
    sql = f"""
        SELECT {CATEGORY_COLUMNS}
        FROM dbo.Category
        WHERE (
            :search IS NULL
            OR CategoryName LIKE N'%' + :search + N'%'
        )
        ORDER BY CategoryID
        OFFSET :offset ROWS
        FETCH NEXT :limit ROWS ONLY
    """

    try:
        with engine.connect() as connection:
            rows = connection.execute(
                text(sql),
                {
                    "search": search,
                    "limit": limit,
                    "offset": offset,
                },
            ).mappings().all()

        return [dict(row) for row in rows]

    except SQLAlchemyError:
        raise HTTPException(
            status_code=503,
            detail="Unable to retrieve categories.",
        )


@router.get("/{category_id}", response_model=CategoryResponse)
def get_category(category_id: int):
    sql = f"""
        SELECT {CATEGORY_COLUMNS}
        FROM dbo.Category
        WHERE CategoryID = :category_id
    """

    try:
        with engine.connect() as connection:
            row = connection.execute(
                text(sql),
                {"category_id": category_id},
            ).mappings().first()

        if row is None:
            raise HTTPException(
                status_code=404,
                detail="Category not found.",
            )

        return dict(row)

    except HTTPException:
        raise
    except SQLAlchemyError:
        raise HTTPException(
            status_code=503,
            detail="Unable to retrieve the category.",
        )


@router.post(
    "",
    response_model=CategoryResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_category(payload: CategoryCreate):
    category_name = validate_category_name(payload.category_name)

    sql = """
        INSERT INTO dbo.Category (CategoryName)
        OUTPUT
            INSERTED.CategoryID AS category_id,
            INSERTED.CategoryName AS category_name
        VALUES (:category_name)
    """

    try:
        with engine.begin() as connection:
            row = connection.execute(
                text(sql),
                {"category_name": category_name},
            ).mappings().one()

        return dict(row)

    except IntegrityError:
        raise HTTPException(
            status_code=409,
            detail="The category conflicts with a database constraint.",
        )
    except SQLAlchemyError:
        raise HTTPException(
            status_code=503,
            detail="Unable to create the category.",
        )


@router.put("/{category_id}", response_model=CategoryResponse)
def replace_category(
    category_id: int,
    payload: CategoryReplace,
):
    category_name = validate_category_name(payload.category_name)

    try:
        with engine.begin() as connection:
            row = connection.execute(
                text("""
                    UPDATE dbo.Category
                    SET CategoryName = :category_name
                    OUTPUT
                        INSERTED.CategoryID AS category_id,
                        INSERTED.CategoryName AS category_name
                    WHERE CategoryID = :category_id
                """),
                {
                    "category_id": category_id,
                    "category_name": category_name,
                },
            ).mappings().first()

            if row is None:
                raise HTTPException(
                    status_code=404,
                    detail="Category not found.",
                )

        return dict(row)

    except HTTPException:
        raise
    except IntegrityError:
        raise HTTPException(
            status_code=409,
            detail="The update conflicts with a database constraint.",
        )
    except SQLAlchemyError:
        raise HTTPException(
            status_code=503,
            detail="Unable to update the category.",
        )


@router.patch("/{category_id}", response_model=CategoryResponse)
def update_category(
    category_id: int,
    payload: CategoryPatch,
):
    changes = payload.model_dump(exclude_unset=True)

    if not changes:
        raise HTTPException(
            status_code=400,
            detail="Provide a category field to update.",
        )

    if "category_name" in changes:
        if changes["category_name"] is None:
            raise HTTPException(
                status_code=422,
                detail="Category name cannot be null.",
            )

        changes["category_name"] = validate_category_name(
            changes["category_name"]
        )

    try:
        with engine.begin() as connection:
            row = connection.execute(
                text("""
                    UPDATE dbo.Category
                    SET CategoryName = :category_name
                    OUTPUT
                        INSERTED.CategoryID AS category_id,
                        INSERTED.CategoryName AS category_name
                    WHERE CategoryID = :category_id
                """),
                {
                    "category_id": category_id,
                    "category_name": changes["category_name"],
                },
            ).mappings().first()

            if row is None:
                raise HTTPException(
                    status_code=404,
                    detail="Category not found.",
                )

        return dict(row)

    except HTTPException:
        raise
    except IntegrityError:
        raise HTTPException(
            status_code=409,
            detail="The update conflicts with a database constraint.",
        )
    except SQLAlchemyError:
        raise HTTPException(
            status_code=503,
            detail="Unable to update the category.",
        )


@router.delete(
    "/{category_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def delete_category(category_id: int):
    try:
        with engine.begin() as connection:
            result = connection.execute(
                text("""
                    DELETE FROM dbo.Category
                    WHERE CategoryID = :category_id
                """),
                {"category_id": category_id},
            )

            if result.rowcount == 0:
                raise HTTPException(
                    status_code=404,
                    detail="Category not found.",
                )

        return Response(status_code=status.HTTP_204_NO_CONTENT)

    except HTTPException:
        raise
    except IntegrityError:
        raise HTTPException(
            status_code=409,
            detail=(
                "This category is assigned to existing products "
                "and cannot be deleted."
            ),
        )
    except SQLAlchemyError:
        raise HTTPException(
            status_code=503,
            detail="Unable to delete the category.",
        )
