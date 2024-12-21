from sqlalchemy import create_engine, Column, Integer, String, Float, ForeignKey
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import relationship

Base = declarative_base()

class Brand(Base):
    __tablename__ = 'brands'
    id = Column(Integer, primary_key=True)
    name = Column(String(50), nullable=False)
    models = relationship("Model", back_populates="brand")

class Model(Base):
    __tablename__ = 'models'
    id = Column(Integer, primary_key=True)
    name = Column(String(50), nullable=False)
    brand_id = Column(Integer, ForeignKey('brands.id'))
    brand = relationship("Brand", back_populates="models")

class Part(Base):
    __tablename__ = 'parts'
    id = Column(Integer, primary_key=True)
    name = Column(String(100), nullable=False)
    brand_id = Column(Integer, ForeignKey('brands.id'))
    model_id = Column(Integer, ForeignKey('models.id'))
    price = Column(Float)
    brand = relationship("Brand")
    model = relationship("Model")