import pandas as pd
from sqlalchemy import create_engine

# Create database connection
engine = create_engine('postgresql://username:password@localhost:5432/your_database')

# Read Excel file
df = pd.read_excel('path/to/your/file.xls')

# Clean column names (remove spaces, special characters)
df.columns = df.columns.str.strip().str.lower().str.replace(' ', '_')

# Import to database
df.to_sql('table_name', engine, if_exists='append', index=False)
