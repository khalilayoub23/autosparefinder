import pandas as pd
from pathlib import Path

# Create sample data matching our column mapping
data = {
    'Customer ID': ['C001', 'C002', 'C003'],
    'Product Name': ['Part A', 'Part B', 'Part C']
}

df = pd.DataFrame(data)
df.to_excel(Path(__file__).parent / 'data' / 'part_data.xlsx', index=False)
