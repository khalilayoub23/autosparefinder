from .base_agent import Agent
from transformers import AutoTokenizer, AutoModel
import torch

class SalesAgent(Agent):
    def __init__(self, name="John"):
        super().__init__(name)
        # Load models only once during initialization
        print(f"Initializing {self.name}...")
        self.device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
        self.tokenizer_bert = AutoTokenizer.from_pretrained('bert-base-multilingual-cased')
        self.model_bert = AutoModel.from_pretrained('bert-base-multilingual-cased').to(self.device)
        print(f"Sales Agent {self.name} is ready on {self.device}!")

    def process_with_bert(self, text):
        with torch.no_grad():
            inputs = self.tokenizer_bert(text, return_tensors="pt", padding=True).to(self.device)
            outputs = self.model_bert(**inputs)
        return outputs.last_hidden_state