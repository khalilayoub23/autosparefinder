from flask import Flask, request, jsonify, render_template
from transformers import BertTokenizer, BertModel
import torch

app = Flask(__name__)

# BERT Handler setup
class MultilingualBERTHandler:
    def __init__(self):
        self.tokenizer = BertTokenizer.from_pretrained('bert-base-multilingual-cased')
        self.model = BertModel.from_pretrained('bert-base-multilingual-cased')
        self.model.eval()
        
    def encode_text(self, text):
        inputs = self.tokenizer(text, return_tensors="pt", padding=True, truncation=True, max_length=512)
        with torch.no_grad():
            outputs = self.model(**inputs)
        return outputs.last_hidden_state.mean(dim=1)

# Initialize BERT
bert_handler = MultilingualBERTHandler()
# Enhanced Agents database
agents = {
    "Sandy": {
        "status": "active",
        "full_name": "Sandra Johnson",
        "role": "Sales Expert",
        "specialty": "Auto Parts Sales"
    },
    "Sam": {
        "status": "active",
        "full_name": "Samuel Wilson",
        "role": "Customer Support",
        "specialty": "Technical Assistance"
    },
    "Oscar": {
        "status": "active",
        "full_name": "Oscar Martinez",
        "role": "Order Management",
        "specialty": "Logistics"
    },
    "Felix": {
        "status": "active",
        "full_name": "Felix Chen",
        "role": "Financial Advisor",
        "specialty": "Parts Pricing"
    },
    "Toby": {
        "status": "active",
        "full_name": "Toby Miller",
        "role": "Technical Expert",
        "specialty": "Engine Components"
    }
}
@app.route('/')
def home():
    return render_template('index.html')

@app.route('/toggle_agent', methods=['POST'])
def toggle_agent():
    try:
        agent_name = request.json.get('agent_name')
        status = request.json.get('status')
        
        if agent_name in agents:
            agents[agent_name]["status"] = status
            return jsonify({"message": f"{agent_name} status updated to {status}"}), 200
        return jsonify({"message": "Agent not found"}), 404
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/agents', methods=['GET'])
def get_agents():
    try:
        return jsonify(agents), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/add_agent', methods=['POST'])
def add_agent():
    try:
        agent_name = request.json.get('agent_name')
        status = request.json.get('status', 'active')
        
        if agent_name in agents:
            return jsonify({"message": "Agent already exists"}), 400
        
        agents[agent_name] = {"status": status}
        return jsonify({"message": f"{agent_name} added successfully"}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/delete_agent', methods=['POST'])
def delete_agent():
    try:
        agent_name = request.json.get('agent_name')
        
        if agent_name not in agents:
            return jsonify({"message": "Agent not found"}), 404
        
        del agents[agent_name]
        return jsonify({"message": f"{agent_name} deleted successfully"}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/process_text', methods=['POST'])
def process_text():
    text = request.json.get('text')
    embeddings = bert_handler.encode_text(text)
    return jsonify({
        "status": "success",
        "embeddings": embeddings.tolist(),
        "text_length": len(text)
    })

@app.route('/docs', methods=['GET'])
def docs():
    docs_info = {
        "endpoints": {
            "/toggle_agent": {
                "method": "POST",
                "description": "Toggle agent status",
                "parameters": {
                    "agent_name": "Name of the agent to update",
                    "status": "'active' or 'inactive'"
                }
            },
            "/agents": {
                "method": "GET",
                "description": "Get all agents and their status"
            },
            "/add_agent": {
                "method": "POST",
                "description": "Add a new agent",
                "parameters": {
                    "agent_name": "Name of the new agent",
                    "status": "Agent status ('active' or 'inactive', default is 'active')"
                }
            },
            "/delete_agent": {
                "method": "POST",
                "description": "Delete an existing agent",
                "parameters": {
                    "agent_name": "Name of the agent to delete"
                }
            },
            "/process_text": {
                "method": "POST",
                "description": "Process text using BERT",
                "parameters": {
                    "text": "Text to process"
                }
            }
        }
    }
    return jsonify(docs_info), 200

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=8501, debug=True)
