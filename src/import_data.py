import pandas as pd
from sqlalchemy import create_engine

def import_sales_data(file_path):
    # Read data
    df = pd.read_excel(file_path)  # or pd.read_csv for CSV files
    
    # Connect to database
    engine = create_engine('postgresql://postgres:postgres@localhost:5432/autosparefinder')
    
    # Import data
    df.to_sql('sales', engine, if_exists='append', index=False)
