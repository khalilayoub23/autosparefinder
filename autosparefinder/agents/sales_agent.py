# ==============================================================================
# ABANDONED — DO NOT USE
# ------------------------------------------------------------------------------
# BERT + GPT-2 pipeline SalesAgent prototype (duplicate of src/ version).
# This was an early NLP experiment before the platform adopted LLM-based agents.
#
# The production sales agent is:
#   backend/BACKEND_AI_AGENTS.py  →  class SalesAgent ("Maya", Ollama/GPT-4o)
#
# Kept for git history only. Do not run, import, or extend this file.
# ==============================================================================

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
