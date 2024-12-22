import pandas as pd
from sqlalchemy import create_engine
import yaml
import logging

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def validate_data(df):
    # Add your validation rules
    required_columns = ['id', 'name', 'value']
    missing_columns = set(required_columns) - set(df.columns)
    if missing_columns:
        raise ValueError(f"Missing required columns: {missing_columns}")
    return True

def import_excel_to_db(excel_path, table_name, mapping_file=None):
    try:
        # Read Excel
        df = pd.read_excel(excel_path)
        
        # Apply column mapping if provided
        if mapping_file:
            with open(mapping_file) as f:
                mapping = yaml.safe_load(f)
            df = df.rename(columns=mapping)
        
        # Validate data
        validate_data(df)
        
        # Connect to database
        engine = create_engine('postgresql://username:password@localhost:5432/your_database')
        
        # Import data
        df.to_sql(table_name, engine, if_exists='append', index=False)
        logger.info(f"Successfully imported {len(df)} rows to {table_name}")
        
    except Exception as e:
        logger.error(f"Error during import: {str(e)}")
        raise
