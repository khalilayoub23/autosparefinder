from config.database import engine, Base
from models.agent import Agent

def init_db():
    Base.metadata.create_all(bind=engine)
