import pandas as pd
from sqlalchemy import create_engine
import yaml
import logging
from pathlib import Path

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def load_mapping():
    mapping_path = Path(__file__).parent / "src" / "column_mapping.yml"
    with open(mapping_path) as f:
        return yaml.safe_load(f)


def import_excel_to_db(excel_path):
    # Load column mapping
    mapping = load_mapping()

    # Read Excel file
    df = pd.read_excel(excel_path)

    # Apply column mapping
    df = df.rename(columns=mapping)

    # Connect to database
    engine = create_engine("postgresql://localhost:5432/autosparefinder")

    # Import data
    df.to_sql("parts", engine, if_exists="append", index=False)
    logger.info(f"Successfully imported {len(df)} rows")


if __name__ == "__main__":
    excel_path = "path/to/your/excel/file.xls"  # Update this path
    import_excel_to_db(excel_path)
