from flask import Flask
from flask_sqlalchemy import SQLAlchemy
from models import db

app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = 'postgresql://localhost/autosparefinder'
db.init_app(app)

if __name__ == "__main__":
    app.run(debug=True)
