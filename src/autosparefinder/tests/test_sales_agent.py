import unittest
from ..agents.sales_agent import SalesAgent

class TestJohnBertCapabilities(unittest.TestCase):
    def setUp(self):
        self.john = SalesAgent(name="John")

    def test_bert_understanding(self):
        # Test multilingual understanding
        hebrew_query = "אני צריך מסנן שמן לטויוטה"
        english_query = "I need an oil filter for Toyota"
        
        hebrew_embedding = self.john.process_with_bert(hebrew_query)
        english_embedding = self.john.process_with_bert(english_query)
        
        # Verify embeddings are generated
        self.assertIsNotNone(hebrew_embedding)
        self.assertIsNotNone(english_embedding)

    def test_intent_classification(self):
        queries = [
            "מה המחיר של המסנן?",
            "איפה אני יכול לאסוף את החלק?",
            "האם יש אחריות על המוצר?"
        ]
        
        for query in queries:
            intent = self.john.process_with_bert(query)
            self.assertIsNotNone(intent)

if __name__ == '__main__':
    unittest.main()
