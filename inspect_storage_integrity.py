from sqlalchemy import text
from app.database import engine

queries = {
    "CHECK CONSTRAINTS": """
        SELECT OBJECT_NAME(parent_object_id) AS TableName, name AS ConstraintName
        FROM sys.check_constraints
        WHERE parent_object_id IN (
            OBJECT_ID('dbo.WarehouseStorageConnections'),
            OBJECT_ID('dbo.WarehouseStorageOAuthStates'),
            OBJECT_ID('dbo.WarehouseDocuments')
        )
        ORDER BY TableName, ConstraintName
    """,
    "INDEXES": """
        SELECT OBJECT_NAME(object_id) AS TableName, name AS IndexName,
               is_unique AS IsUnique
        FROM sys.indexes
        WHERE object_id IN (
            OBJECT_ID('dbo.WarehouseStorageConnections'),
            OBJECT_ID('dbo.WarehouseStorageOAuthStates'),
            OBJECT_ID('dbo.WarehouseDocuments')
        )
          AND name IS NOT NULL
        ORDER BY TableName, IndexName
    """,
    "FOREIGN KEYS": """
        SELECT OBJECT_NAME(parent_object_id) AS TableName,
               name AS ForeignKeyName,
               OBJECT_NAME(referenced_object_id) AS ReferencedTable
        FROM sys.foreign_keys
        WHERE parent_object_id IN (
            OBJECT_ID('dbo.WarehouseStorageConnections'),
            OBJECT_ID('dbo.WarehouseStorageOAuthStates'),
            OBJECT_ID('dbo.WarehouseDocuments')
        )
        ORDER BY TableName, ForeignKeyName
    """
}

with engine.connect() as connection:
    for title, sql in queries.items():
        print(f"\n--- {title} ---")
        for row in connection.execute(text(sql)):
            print(" | ".join(str(value) for value in row))
