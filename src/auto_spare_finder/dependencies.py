from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

DATABASE_URL = "postgresql://user:password@localhost:5432/sparefinder"
engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(bind=engine)
