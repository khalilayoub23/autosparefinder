from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from .models import Base

# Create database engine with absolute path
engine = create_engine('sqlite:////workspaces/autosparefinder/database/spare_parts.db')

# Create all tables
Base.metadata.create_all(engine)

# Create session factory
Session = sessionmaker(bind=engine)

def init_db():
    # Create a new session
    session = Session()
    
    try:
        # Add sample data if needed
        # session.add(...)
        session.commit()
    except Exception as e:
        print(f"Error initializing database: {e}")
        session.rollback()
    finally:
        session.close()

if __name__ == '__main__':
    init_db()