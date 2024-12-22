from .base_agent import Agent
from transformers import AutoTokenizer, AutoModel
import torch
from sklearn.linear_model import LogisticRegression
import joblib
import numpy as np


class SalesAgent(Agent):
    def __init__(self, name="John"):
        super().__init__(name)
        # Load models only once during initialization
        print(f"Initializing {self.name}...")
        self.device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        self.tokenizer_bert = AutoTokenizer.from_pretrained(
            "bert-base-multilingual-cased"
        )
        self.model_bert = AutoModel.from_pretrained("bert-base-multilingual-cased").to(
            self.device
        )

        # Initialize or load a machine learning model
        self.classifier = LogisticRegression()
        self.load_model()

        print(f"Sales Agent {self.name} is ready on {self.device}!")

    def process_with_bert(self, text):
        with torch.no_grad():
            inputs = self.tokenizer_bert(text, return_tensors="pt", padding=True).to(
                self.device
            )
            outputs = self.model_bert(**inputs)
        return outputs.last_hidden_state.mean(dim=1).cpu().numpy()

    def train_model(self, texts, labels):
        print("Training started...")
        # Extract features using BERT
        features = np.vstack([self.process_with_bert(text) for text in texts])
        # Train the machine learning model
        self.classifier.fit(features, labels)
        self.save_model()
        print("Training completed.")

    def predict(self, text):
        # Extract features using BERT
        features = self.process_with_bert(text)
        # Predict using the machine learning model
        return self.classifier.predict(features)[0]

    def save_model(self):
        # Save the trained model
        joblib.dump(self.classifier, "classifier.joblib")

    def load_model(self):
        # Load the trained model if it exists
        try:
            self.classifier = joblib.load("classifier.joblib")
        except FileNotFoundError:
            print("Model file not found. Train the model first.")
