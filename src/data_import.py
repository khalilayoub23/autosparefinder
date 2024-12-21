import pandas as pd
from sqlalchemy import create_engine
from config.database import engine

def import_parts_data():
    # Read the Excel file
    df = pd.read_excel('parts_data.xlsx')
    
    # Clean and transform data as needed
    df.columns = df.columns.str.lower().str.replace(' ', '_')
    
    # Create parts table and import data
    df.to_sql('parts', engine, if_exists='replace', index=False)
    
    print(f"Successfully imported {len(df)} parts records")

if __name__ == "__main__":
    import_parts_data()
