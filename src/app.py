from flask import Flask, jsonify

# Create app instance
app = Flask(__name__)

@app.route('/')
def root():
    return jsonify({
        "status": "healthy",
        "version": "1.0.0"
    })

@app.route('/api/parts')
def get_parts():
    return jsonify({
        "parts": [
            {"id": 1, "name": "Engine", "category": "ENGINE"},
            {"id": 2, "name": "Transmission", "category": "TRANSMISSION"}
        ]
    })

@app.route('/api/agents')
def get_agents():
    return jsonify({
        "agents": [
            {"id": 1, "name": "John Doe", "email": "john@example.com"}
        ]
    })

@app.route('/api/status')
def status():
    return jsonify({
        "database": "connected",
        "endpoints": [
            "/api/parts",
            "/api/status"
        ]
    })

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=5000, debug=True)