from sqlalchemy import text
from app.database import engine

sql = """
SELECT
    fk.name AS ForeignKeyName,
    pc.name AS SourceColumn,
    rc.name AS ReferencedColumn,
    fkc.constraint_column_id AS ColumnOrder
FROM sys.foreign_keys AS fk
JOIN sys.foreign_key_columns AS fkc
    ON fkc.constraint_object_id = fk.object_id
JOIN sys.columns AS pc
    ON pc.object_id = fkc.parent_object_id
   AND pc.column_id = fkc.parent_column_id
JOIN sys.columns AS rc
    ON rc.object_id = fkc.referenced_object_id
   AND rc.column_id = fkc.referenced_column_id
WHERE fk.name = 'FK_WarehouseDocuments_StorageConnection'
ORDER BY fkc.constraint_column_id;
"""

with engine.connect() as connection:
    for row in connection.execute(text(sql)):
        print(
            f"{row.ForeignKeyName}: "
            f"{row.SourceColumn} -> {row.ReferencedColumn} "
            f"(column {row.ColumnOrder})"
        )
