from .base_agent import Agent
from transformers import AutoTokenizer, AutoModel, pipeline
import torch


class SalesAgent(Agent):
    def __init__(self, name="John"):
        super().__init__(name)
        self.tokenizer_bert = AutoTokenizer.from_pretrained(
            "bert-base-multilingual-cased"
        )
        self.model_bert = AutoModel.from_pretrained("bert-base-multilingual-cased")
        self.nlp_pipeline = pipeline("text-generation", model="gpt2")
        print(f"Sales Agent {self.name} is ready to help!")

    def process_with_bert(self, text):
        inputs = self.tokenizer_bert(text, return_tensors="pt", padding=True)
        outputs = self.model_bert(**inputs)
        return outputs.last_hidden_state
