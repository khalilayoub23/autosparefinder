from sqlalchemy import create_engine, inspect

# Create engine
engine = create_engine('postgresql://postgres:postgres@localhost:5432/autosparefinder')

# Create inspector
inspector = inspect(engine)

# Get all table names
tables = inspector.get_table_names()
print("Existing tables:", tables)

# For each table, get column information
for table_name in tables:
    print(f"\nTable: {table_name}")
    for column in inspector.get_columns(table_name):
        print(f"Column: {column['name']}, Type: {column['type']}")
