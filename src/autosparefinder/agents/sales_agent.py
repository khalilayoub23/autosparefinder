from .base_agent import Agent  # Use relative import
from transformers import AutoTokenizer, AutoModel, pipeline
import torch

class SalesAgent(Agent):
    def __init__(self, name="John"):
        super().__init__(name)
        self.tokenizer_bert = AutoTokenizer.from_pretrained('bert-base-multilingual-cased')
        self.model_bert = AutoModel.from_pretrained('bert-base-multilingual-cased')
        self.nlp_pipeline = pipeline("text-generation", model="gpt2")
        print(f"Sales Agent {self.name} is ready to help!")
        
    def process_with_bert(self, text):
        inputs = self.tokenizer_bert(text, return_tensors="pt", padding=True)
        outputs = self.model_bert(**inputs)
        return outputs.last_hidden_state        
    def chat_with_customer(self, customer_id, message):
        # Process customer message using NLP
        response = self.nlp_pipeline(message)[0]['generated_text']
        if customer_id not in self.chat_history:
            self.chat_history[customer_id] = []
        self.chat_history[customer_id].append({"customer": message, "agent": response})
        return response

    def process_barcode(self, barcode_image):
        # Decode barcode from image
        decoded_barcode = cv2.imread(barcode_image)
        detector = cv2.barcode.BarcodeDetector()
        retval, decoded_info, decoded_type, points = detector.detectAndDecode(decoded_barcode)
        return decoded_info

    def process_part_image(self, image_path):
        # OCR processing for part images
        image = Image.open(image_path)
        text = pytesseract.image_to_string(image)
        return self.identify_part_from_text(text)

    def process_voice_input(self, audio_file):
        # Convert speech to text
        with sr.AudioFile(audio_file) as source:
            audio = self.speech_recognizer.record(source)
            try:
                text = self.speech_recognizer.recognize_google(audio, language='he-IL')
                return text
            except sr.UnknownValueError:
                return "Could not understand audio"

    def identify_part_from_text(self, text):
        # Match text with parts database
        matches = []
        for part_id, part_info in self.parts_database.items():
            if text.lower() in part_info['description'].lower():
                matches.append(part_info)
        return matches

    def process_order(self, customer_id, part_id, quantity):
        # Order processing logic
        order = {
            'customer_id': customer_id,
            'part_id': part_id,
            'quantity': quantity,
            'status': 'pending'
        }
        return self.confirm_order(order)

    def confirm_order(self, order):
        # Order confirmation logic
        order['status'] = 'confirmed'
        return order

    def execute_task(self, task_type, **params):
        if task_type == "chat":
            return self.chat_with_customer(params['customer_id'], params['message'])
        elif task_type == "barcode":
            return self.process_barcode(params['barcode_image'])
        elif task_type == "image":
            return self.process_part_image(params['image_path'])
        elif task_type == "voice":
            return self.process_voice_input(params['audio_file'])
        elif task_type == "order":
            return self.process_order(params['customer_id'], params['part_id'], params['quantity'])

import streamlit as st
import sys
import os

# Add the parent directory to the Python path
current_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.dirname(current_dir)
sys.path.insert(0, parent_dir)

from agents.sales_agent import SalesAgent  # Ensure this import is correct
