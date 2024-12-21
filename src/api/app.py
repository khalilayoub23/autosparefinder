from flask import Flask, render_template, jsonify, request
from flask_cors import CORS
import datetime
import time
from transformers import BertTokenizer, BertModel
import torch
import streamlit as st
import pandas as pd
import sqlite3
from agents.sales_agent import SalesAgent
from sqlalchemy import create_engine, Column, Integer, String, Float, ForeignKey
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import relationship, sessionmaker

Base = declarative_base()

class Category(Base):
    __tablename__ = 'categories'
    id = Column(Integer, primary_key=True)
    name = Column(String(50), nullable=False)
    parts = relationship("Part", back_populates="category")

class Brand(Base):
    __tablename__ = 'brands'
    id = Column(Integer, primary_key=True)
    name = Column(String(50), nullable=False)
    models = relationship("Model", back_populates="brand")

class Model(Base):
    __tablename__ = 'models'
    id = Column(Integer, primary_key=True)
    name = Column(String(50), nullable=False)
    brand_id = Column(Integer, ForeignKey('brands.id'))
    brand = relationship("Brand", back_populates="models")
    parts = relationship("Part", back_populates="model")

class Part(Base):
    __tablename__ = 'parts'
    id = Column(Integer, primary_key=True)
    name = Column(String(100), nullable=False)
    category_id = Column(Integer, ForeignKey('categories.id'))
    brand_id = Column(Integer, ForeignKey('brands.id'))
    model_id = Column(Integer, ForeignKey('models.id'))
    price = Column(Float)
    category = relationship("Category", back_populates="parts")
    brand = relationship("Brand")
    model = relationship("Model", back_populates="parts")

tokenizer = BertTokenizer.from_pretrained('bert-base-uncased')
model = BertModel.from_pretrained('bert-base-uncased')

def encode_text(text):
    inputs = tokenizer(text, return_tensors='pt')
    outputs = model(**inputs)
    return outputs.last_hidden_state.mean(dim=1).detach().numpy()

bert_handler = type('BertHandler', (object,), {'encode_text': encode_text})()

app = Flask(__name__)
CORS(app)

# Set target completion dates
target_dates = {
    "google_ads": datetime.datetime(2024, 3, 15),  # Target date for Google Ads mastery
    "social_media": datetime.datetime(2024, 3, 30)  # Target date for Social Media mastery
}

# Initialize agents including Mark
agents = {
    "Mark": {
        "status": "active",
        "full_name": "Mark Thompson",
        "role": "Marketing Specialist",
        "specialty": "Digital Marketing",
        "learning_progress": {
            "google_ads": 45,
            "social_media": 60,
            "campaign_planning": 30
        }
    },
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
    now = datetime.datetime.now()
    google_ads_remaining = target_dates["google_ads"] - now
    social_media_remaining = target_dates["social_media"] - now
    
    return f"""
    <html>
        <head>
            <title>Marketing Agent Progress</title>
            <style>
                body {{ font-family: Arial, sans-serif; margin: 40px; }}
                .dashboard {{ background: #fff; padding: 20px; border-radius: 10px; box-shadow: 0 0 10px rgba(0,0,0,0.1); }}
                .agent-card {{ background: #f5f5f5; padding: 15px; margin: 10px 0; border-radius: 8px; }}
                .progress-bar {{ background: #ddd; height: 20px; border-radius: 10px; }}
                .progress {{ background: #4CAF50; height: 100%; border-radius: 10px; }}
                .timer {{ 
                    background: #ff4757; 
                    color: white; 
                    padding: 10px; 
                    border-radius: 5px;
                    font-size: 18px;
                    margin: 10px 0;
                }}
            </style>
            <script>
                function updateTimer() {{
                    const googleTarget = new Date('{target_dates["google_ads"].isoformat()}');
                    const socialTarget = new Date('{target_dates["social_media"].isoformat()}');
                    
                    setInterval(() => {{
                        const now = new Date();
                        document.getElementById('google-timer').innerText = 
                            Math.floor((googleTarget - now) / (1000 * 60 * 60 * 24)) + ' days remaining';
                        document.getElementById('social-timer').innerText = 
                            Math.floor((socialTarget - now) / (1000 * 60 * 60 * 24)) + ' days remaining';
                    }}, 1000);
                }}
            </script>
        </head>
        <body onload="updateTimer()">
            <div class="dashboard">
                <h1>Mark's Learning Progress</h1>
                
                <div class="metric-card">
                    <h2>Google Ads Certification</h2>
                    <div class="timer" id="google-timer">{google_ads_remaining.days} days remaining</div>
                </div>

                <div class="metric-card">
                    <h2>Social Media Mastery</h2>
                    <div class="timer" id="social-timer">{social_media_remaining.days} days remaining</div>
                </div>

                <div class="agent-card">
                    <h2>Mark Thompson</h2>
                    <p><strong>Role:</strong> Marketing Specialist</p>
                    <p><strong>Current Course:</strong> Google Ads Fundamentals</p>
                    <p><strong>Progress:</strong></p>
                    <div class="progress-bar">
                        <div class="progress" style="width: 45%"></div>
                    </div>
                    <p><strong>Active Learning:</strong> Yes</p>
                    <p><strong>Completed Modules:</strong></p>
                    <ul>
                        <li>Digital Marketing Basics</li>
                        <li>Search Advertising</li>
                        <li>Campaign Planning</li>
                    </ul>
                </div>
            </div>
        </body>
    </html>
    """

@app.route('/toggle_agent', methods=['POST'])
def toggle_agent():
    try:
        agent_name = request.json.get('agent_name')
        status = request.json.get('status')
    except Exception as e:
        return jsonify({"error": str(e)}), 500
if __name__ == "__main__":
    print("Server starting on http://127.0.0.1:8501")
    app.run(port=8501, debug=True)

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
        data = request.json
        agent_name = data.get('agent_name')
        
        if agent_name in agents:
            return jsonify({"message": "Agent already exists"}), 400
        
        agents[agent_name] = {
            "status": data.get('status', 'active'),
            "full_name": data.get('full_name', ''),
            "role": data.get('role', ''),
            "specialty": data.get('specialty', '')
        }
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

@app.route('/monitor_mark')
def monitor_mark():
    return jsonify(agents["Mark"]), 200

if __name__ == "__main__":
    print("Starting server on port 8501...")
    app.run(port=8501, debug=True)

# Function to read Excel file and insert data into the database
def load_data_to_db(file_path, db_path):
    # Read the Excel file
    df = pd.read_excel(file_path)
    
    # Connect to the SQLite database
    conn = sqlite3.connect(db_path)
    
    # Insert data into the database
    df.to_sql('parts_data', con=conn, if_exists='replace', index=False)
    
    # Close the connection
    conn.close()

# Load data into the database
file_path = 'C:\\Users\\khalil ayoub\\Desktop\\car brands database\\parts data base.xlsx'
db_path = 'fastpost.db'
load_data_to_db(file_path, db_path)

import streamlit as st
from agents.sales_agent import SalesAgent
from sqlalchemy.orm import sessionmaker

# Create database engine
engine = create_engine('sqlite:///spare_parts.db')

# Create session
Session = sessionmaker(bind=engine)
from database.models import Part, Category, Brand, Model

def main():
    st.title("Spare Parts Finder")
    
    # Database session
    session = Session()
    
    # Initialize sales agent
    john = SalesAgent("John")
    
    # Sidebar filters
    category = st.sidebar.selectbox("Category", [c.name for c in session.query(Category).all()])
    brand = st.sidebar.selectbox("Brand", [b.name for b in session.query(Brand).all()])
    
    # Create the chat interface
    user_input = st.text_input("You:", "")
    
    if user_input:
        response = john.respond(user_input)
        st.text(f"John: {response}")
        
        # Query parts based on filters
        parts = session.query(Part).join(Category).join(Brand)\
            .filter(Category.name == category)\
            .filter(Brand.name == brand)\
            .all()
            
        # Display parts
        for part in parts:
            st.write(f"{part.name} - ${part.price}")

if __name__ == "__main__":
    main()

from .database.models import Base, Category, Brand, Model, Part

# Create database engine
engine = create_engine('sqlite:///spare_parts.db')

# Create tables
Base.metadata.create_all(engine)

# Create session
Session = sessionmaker(bind=engine)
session = Session()

# Add sample data
def add_sample_data():
    # Categories
    categories = [
        Category(name='Engine'),
        Category(name='Transmission'),
        Category(name='Brakes'),
        Category(name='Suspension')
    ]
    session.add_all(categories)
    
    # Brands
    brands = [
        Brand(name='Toyota'),
        Brand(name='Honda'),
        Brand(name='BMW')
    ]
    session.add_all(brands)
    
    session.commit()

if __name__ == '__main__':
    add_sample_data()
# Directory structure
# autosparefinder/
# ├── database/
# │   ├── __init__.py
# │   ├── models.py
# │   └── db_setup.py
# ├── app.py
# └── requirements.txt
