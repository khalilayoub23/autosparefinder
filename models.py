from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()

class Part(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    customer_id = db.Column(db.String(100))
    product_name = db.Column(db.String(200))
