# ==============================================================================
# ABANDONED — DO NOT USE
# ------------------------------------------------------------------------------
# This file is a pre-architecture skeleton from an earlier prototype.
# It uses Integer PKs, legacy declarative_base(), and 3 stub tables
# (brands, models, parts) that are NOT connected to any database, engine,
# session, or Alembic instance in this project.
#
# The live schema is defined exclusively in:
#   backend/BACKEND_DATABASE_MODELS.py
#
# This file is kept only to preserve git history. Do not import from it,
# do not run it, and do not add new code here.
# ==============================================================================

from sqlalchemy import create_engine, Column, Integer, String, Float, ForeignKey
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import relationship

Base = declarative_base()


class Brand(Base):
    __tablename__ = "brands"
    id = Column(Integer, primary_key=True)
    name = Column(String(50), nullable=False)
    models = relationship("Model", back_populates="brand")


class Model(Base):
    __tablename__ = "models"
    id = Column(Integer, primary_key=True)
    name = Column(String(50), nullable=False)
    brand_id = Column(Integer, ForeignKey("brands.id"))
    brand = relationship("Brand", back_populates="models")


class Part(Base):
    __tablename__ = "parts"
    id = Column(Integer, primary_key=True)
    name = Column(String(100), nullable=False)
    brand_id = Column(Integer, ForeignKey("brands.id"))
    model_id = Column(Integer, ForeignKey("models.id"))
    price = Column(Float)
    brand = relationship("Brand")
    model = relationship("Model")
