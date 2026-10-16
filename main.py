import logging
from app.api.inventory_balances import router as inventory_balances_router
from app.api.stock_counts import router as stock_counts_router
from app.api.sales_invoices import router as sales_invoices_router
from app.api.categories import router as categories_router
from app.api.warehouses import router as warehouses_router
from app.api.customer import router as customers_router
from app.api.products import router as products_router
from app.api.inventory import router as inventory_router
from app.api.suppliers import router as suppliers_router
from fastapi import FastAPI, HTTPException, Path
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.database import engine, settings


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="Kijana Finance",
    description="Finance application API backed by SQL Server.",
    version="1.0.0",
)
app.include_router(sales_invoices_router)
app.include_router(products_router)
app.include_router(inventory_router)
app.include_router(customers_router)
app.include_router(warehouses_router)
app.include_router(suppliers_router)
app.include_router(categories_router)
app.include_router(stock_counts_router)
app.include_router(inventory_balances_router)

@app.get("/health")
def health_check():
    return {
        "status": "ok",
        "service": "Kijana Finance",
    }


@app.get("/database/schema-summary")
def get_schema_summary():
    query = text("""
        SELECT
            s.name AS schema_name,
            t.name AS table_name
        FROM sys.tables AS t
        INNER JOIN sys.schemas AS s
            ON s.schema_id = t.schema_id
        WHERE t.is_ms_shipped = 0
        ORDER BY s.name, t.name;
    """)

    try:
        with engine.connect() as connection:
            result = connection.execute(query)
            tables = [
                {
                    "schema": row.schema_name,
                    "table": row.table_name,
                }
                for row in result
            ]

        return {
            "status": "connected",
            "database": settings.database,
            "table_count": len(tables),
            "tables": tables,
        }

    except SQLAlchemyError:
        logger.exception("Database schema inspection failed.")
        raise HTTPException(
            status_code=503,
            detail="The database inspection could not be completed.",
        )



@app.get("/journals/{journal_id}")
def get_journal(
    journal_id: int = Path(gt=0, description="The journal's ID"),
):
    header_query = text("""
        SELECT
            je.JournalID,
            je.JournalNumber,
            je.PostingDate,
            je.SourceType,
            je.SourceID,
            je.Description,
            je.Status,
            je.PeriodID
        FROM dbo.JournalEntries AS je
        WHERE je.JournalID = :journal_id;
    """)

    lines_query = text("""
        SELECT
            jel.JournalLineID,
            jel.AccountCode,
            coa.AccountName,
            jel.Debit,
            jel.Credit,
            jel.CustomerID,
            jel.SupplierID
        FROM dbo.JournalEntryLines AS jel
        INNER JOIN dbo.ChartOfAccounts AS coa
            ON coa.AccountCode = jel.AccountCode
        WHERE jel.JournalID = :journal_id
        ORDER BY jel.JournalLineID;
    """)

    try:
        with engine.connect() as connection:
            header_result = connection.execute(
                header_query,
                {"journal_id": journal_id},
            )
            header = header_result.mappings().first()

            if header is None:
                raise HTTPException(
                    status_code=404,
                    detail="Journal entry not found.",
                )

            lines_result = connection.execute(
                lines_query,
                {"journal_id": journal_id},
            )
            lines = [
                dict(row)
                for row in lines_result.mappings()
            ]

        return {
            "journal": dict(header),
            "lines": lines,
        }

    except HTTPException:
        raise

    except SQLAlchemyError:
        logger.exception("Journal inspection failed.")
        raise HTTPException(
            status_code=503,
            detail="Journal information is temporarily unavailable.",
        )
