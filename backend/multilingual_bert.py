from transformers import BertTokenizer, BertModel
import torch

class MultilingualBERTHandler:
    def __init__(self):
        self.tokenizer = BertTokenizer.from_pretrained('bert-base-multilingual-cased')
        self.model = BertModel.from_pretrained('bert-base-multilingual-cased')
        self.model.eval()  # Set to evaluation mode
        
    def encode_text(self, text):
        # Tokenize and encode the text
        inputs = self.tokenizer(text, return_tensors="pt", padding=True, truncation=True, max_length=512)
        with torch.no_grad():
            outputs = self.model(**inputs)
        return outputs.last_hidden_state.mean(dim=1)  # Get sentence embedding

# Initialize the model
bert_handler = MultilingualBERTHandler()
