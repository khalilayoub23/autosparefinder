from agents.sales_agent import SalesAgent

# Create John instance
john = SalesAgent()

# Test customer queries
queries = [
    "אני מחפש חלקי חילוף לרכב שלי",
    "How much does an oil filter cost?",
    "תראה לי בבקשה מסנני אוויר למאזדה"
]

# Process each query with BERT
for query in queries:
    result = john.process_with_bert(query)
    print(f"Query: {query}")
    print(f"BERT Output Shape: {result.shape}")
    print("---")
