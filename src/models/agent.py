from sqlalchemy import Column, Integer, String
from config.database import Base

class Agent(Base):
    __tablename__ = "agents"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, unique=True, index=True)
    email = Column(String, unique=True)
    phone = Column(String)
    address = Column(String)
