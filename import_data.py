import pandas as pd
from app import app, db
from models import Part

def import_xls_data(file_path):
    with app.app_context():
        try:
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
    import_xls_data('data/parts.xls')
