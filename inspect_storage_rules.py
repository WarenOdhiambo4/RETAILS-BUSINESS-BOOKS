from sqlalchemy import text
from app.database import engine

sql = """
SELECT
    OBJECT_NAME(parent_object_id) AS TableName,
    name AS ConstraintName,
    definition AS RuleDefinition
FROM sys.check_constraints
WHERE parent_object_id IN (
    OBJECT_ID('dbo.WarehouseStorageConnections'),
    OBJECT_ID('dbo.WarehouseStorageOAuthStates')
)
ORDER BY TableName, ConstraintName;

SELECT
    fk.name AS ForeignKeyName,
    pc.name AS SourceColumn,
    OBJECT_NAME(fk.referenced_object_id) AS ReferencedTable,
    rc.name AS ReferencedColumn,
    fkc.constraint_column_id AS ColumnOrder
FROM sys.foreign_keys AS fk
INNER JOIN sys.foreign_key_columns AS fkc
    ON fkc.constraint_object_id = fk.object_id
INNER JOIN sys.columns AS pc
    ON pc.object_id = fkc.parent_object_id
   AND pc.column_id = fkc.parent_column_id
INNER JOIN sys.columns AS rc
    ON rc.object_id = fkc.referenced_object_id
   AND rc.column_id = fkc.referenced_column_id
WHERE fk.parent_object_id = OBJECT_ID('dbo.WarehouseDocuments')
  AND fk.name = 'FK_WarehouseDocuments_StorageConnection'
ORDER BY fkc.constraint_column_id;
"""

with engine.connect() as connection:
    for result in connection.execute(text(sql)).all():
        print(" | ".join(str(value) for value in result))
