# ==============================================================================
# ABANDONED — DO NOT USE
# ------------------------------------------------------------------------------
# Interactive REPL test script for the BERT-based SalesAgent prototype ("John").
# Not a pytest test — just a manual demonstration script.
# Has no connection to the production agent stack or test suite.
#
# The production agent tests are in:
#   backend/tests/  and  backend/test_agents_full_cycle.py
#
# Kept for git history only. Do not run, import, or extend this file.
# ==============================================================================

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
