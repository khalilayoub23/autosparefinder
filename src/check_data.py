import os
import pandas as pd

# Find and read XLS files
for root, dirs, files in os.walk('.'):
    for file in files:
        if file.endswith(('.xls', '.xlsx')):
            file_path = os.path.join(root, file)
            print(f"\nReading file: {file_path}")
            df = pd.read_excel(file_path)
            print("\nFirst 5 rows:")
            print(df.head())
            print("\nColumns:", df.columns.tolist())
