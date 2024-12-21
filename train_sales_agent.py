import pandas as pd
from agents.sales_agent import SalesAgent

# Google Sheets URL (export as CSV)
sheet_url = 'https://docs.google.com/spreadsheets/d/1f_CF4lDBaB5m5_w1Ca4yQFS5LwLhSuhy/export?format=csv'

# Read the data from Google Sheets
df = pd.read_csv(sheet_url)

# Assuming the Google Sheets document has columns 'text' and 'label'
texts = df['text'].tolist()
labels = df['label'].tolist()

# Initialize the SalesAgent
agent = SalesAgent("John")

# Train the model
print("Training started...")
agent.train_model(texts, labels)
print("Training completed.")
