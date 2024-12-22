import pandas as pd
from flask import Flask
from models import db, Part
import os

app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = 'postgresql://localhost/autosparefinder'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db.init_app(app)

def import_xls_data():
    try:
        file_path = os.path.join('data', 'part_data.xlsx')
        df = pd.read_excel(file_path)
        imported_count = 0
        
        for _, row in df.iterrows():
            part = Part(
                customer_id=row['Customer ID'],
                product_name=row['Product Name']
            )
            db.session.add(part)
            imported_count += 1
        
        db.session.commit()
        print(f"Successfully imported {imported_count} parts")
        
    except Exception as e:
        db.session.rollback()
        print(f"Error importing data: {str(e)}")

if __name__ == "__main__":
    with app.app_context():
        import_xls_data()        import_xls_data()