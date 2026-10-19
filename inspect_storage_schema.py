from sqlalchemy import text
from app.database import engine

with engine.connect() as connection:
    rows = connection.execute(text("""
        SELECT TABLE_NAME, COLUMN_NAME, DATA_TYPE
        FROM INFORMATION_SCHEMA.COLUMNS
        WHERE TABLE_SCHEMA = 'dbo'
          AND TABLE_NAME IN (
              'WarehouseStorageConnections',
              'WarehouseStorageOAuthStates',
              'AuditLog'
          )
        ORDER BY TABLE_NAME, ORDINAL_POSITION
    """)).all()

    for row in rows:
        print(f"{row[0]}.{row[1]} ({row[2]})")
