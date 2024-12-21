import argparse
import pandas as pd
import sys

def validate_csv_format(file_path):
    try:
        # Read the CSV file
        df = pd.read_csv(file_path)
        
        # Expected columns
        required_columns = [
            'part_number',
            'description',
            'manufacturer',
            'price',
            'stock_quantity',
            'category'
        ]
        
        # Check columns
        missing_columns = [col for col in required_columns if col not in df.columns]
        if missing_columns:
            print(f"Missing columns: {missing_columns}")
            return False
            
        # Data validation
        print("Validating data format...")
        print(f"Total rows: {len(df)}")
        print(f"Valid part numbers: {df['part_number'].notna().sum()}")
        print(f"Valid prices: {df['price'].notna().sum()}")
        
        return True
        
    except Exception as e:
        print(f"Validation error: {str(e)}")
        return False

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='Validate sales data CSV format')
    parser.add_argument('--input', required=True, help='Input CSV file path')
    parser.add_argument('--check-format', action='store_true', help='Check CSV format')
    
    args = parser.parse_args()
    validate_csv_format(args.input)
