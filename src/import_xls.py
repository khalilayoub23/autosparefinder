import pandas as pd
from sqlalchemy import create_engine

def import_sales_data(xls_path):
    # Read the XLS file
    df = pd.read_excel(xls_path)
    
    # Display first few rows and data info
    print("\nFirst few rows of data:")
    print(df.head())
    print("\nData Info:")
    print(df.info())
    
    # Connect and import to database
    engine = create_engine('postgresql://postgres:postgres@localhost:5432/autosparefinder')
    df.to_sql('sales', engine, if_exists='append', index=False)
