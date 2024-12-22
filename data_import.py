import pandas as pd
from flask_sqlalchemy import SQLAlchemy
from models import Part, db

def import_xls_data(file_path):
    df = pd.read_excel(file_path)
    
    for _, row in df.iterrows():
        part = Part(
            customer_id=row['Customer ID'],
            product_name=row['Product Name']
        )
        db.session.add(part)
    
    db.session.commit()
