class Agent:
    def __init__(self, name):
        self.name = name

    def respond(self, input_text):
        embeddings = self.process_with_bert(input_text)
        return self.generate_response(embeddings)

    def generate_response(self, embeddings):
        # Convert embeddings to meaningful responses
        # Add sales-specific logic here
        return f"Hello, I'm {self.name}. How can I assist you today?"
