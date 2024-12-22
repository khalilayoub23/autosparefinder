import pandas as pd

# Create sample data matching our column mapping
data = {
    'Customer ID': ['C001', 'C002', 'C003'],
    'Product Name': ['Part A', 'Part B', 'Part C']
}

df = pd.DataFrame(data)
df.to_excel('data/part_data.xlsx', index=False)
