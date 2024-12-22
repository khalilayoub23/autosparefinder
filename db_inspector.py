from sqlalchemy import create_engine, inspect


def inspect_database():
    engine = create_engine("postgresql://localhost:5432/autosparefinder")
    inspector = inspect(engine)

    print("Database Schema Overview:")
    print("-----------------------")

    for table_name in inspector.get_table_names():
        print(f"\nTable: {table_name}")
        print("Columns:")
        for column in inspector.get_columns(table_name):
            print(f"- {column['name']}: {column['type']}")

        print("\nIndexes:")
        for index in inspector.get_indexes(table_name):
            print(f"- {index['name']}: {index['column_names']}")


if __name__ == "__main__":
    inspect_database()
