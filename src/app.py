from flask import Flask, jsonify
import sqlite3

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
    conn = sqlite3.connect('src/autosparefinder.db')
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM parts LIMIT 10')  # Get first 10 parts for testing
    columns = [description[0] for description in cursor.description]
    parts = []
    
    for row in cursor.fetchall():
        part = {}
        for i, column in enumerate(columns):
            part[column] = row[i]
        parts.append(part)
    
    conn.close()
    return jsonify({
        "parts": parts,
        "total_shown": len(parts),
        "columns": columns
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

@app.route('/api/parts/count')
def get_parts_count():
    conn = sqlite3.connect('src/autosparefinder.db')
    cursor = conn.cursor()
    cursor.execute('SELECT COUNT(*) FROM parts')
    count = cursor.fetchone()[0]
    conn.close()
    return jsonify({
        "total_parts": count,
        "status": "success"
    })

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=5000, debug=True)
