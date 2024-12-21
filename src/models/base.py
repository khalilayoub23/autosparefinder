from sqlalchemy import (
    Column, Integer, String, Float, ForeignKey, DateTime, 
    Enum, Boolean, Table, Text, UniqueConstraint
)
from sqlalchemy.orm import relationship
from sqlalchemy.ext.hybrid import hybrid_property
import enum
from datetime import datetime
from config.database import Base

class PartCategory(enum.Enum):
    ENGINE = "engine"
    TRANSMISSION = "transmission"
    SUSPENSION = "suspension"
    ELECTRICAL = "electrical"
    BODY = "body"
    INTERIOR = "interior"

class OrderStatus(enum.Enum):
    PENDING = "pending"
    CONFIRMED = "confirmed"
    SHIPPED = "shipped"
    DELIVERED = "delivered"
    CANCELLED = "cancelled"

class Agent(Base):
    __tablename__ = "agents"
    id = Column(Integer, primary_key=True)
    name = Column(String(100), nullable=False)
    email = Column(String(100), unique=True, nullable=False)
    phone = Column(String(20))
    address = Column(Text)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, onupdate=datetime.utcnow)
    
    # Relationships
    inventory = relationship("Inventory", back_populates="agent")
    orders = relationship("Order", back_populates="agent")
    specializations = relationship("PartCategory", secondary="agent_specializations")

class Part(Base):
    __tablename__ = "parts"
    id = Column(Integer, primary_key=True)
    name = Column(String(200), nullable=False)
    part_number = Column(String(50), unique=True, nullable=False)
    category = Column(Enum(PartCategory))
    manufacturer = Column(String(100))
    description = Column(Text)
    specifications = Column(Text)
    weight = Column(Float)
    dimensions = Column(String(50))
    created_at = Column(DateTime, default=datetime.utcnow)
    
    # Price tracking
    base_price = Column(Float, nullable=False)
    markup_percentage = Column(Float, default=20.0)
    
    @hybrid_property
    def retail_price(self):
        return self.base_price * (1 + self.markup_percentage / 100)

class Inventory(Base):
    __tablename__ = "inventory"
    id = Column(Integer, primary_key=True)
    agent_id = Column(Integer, ForeignKey("agents.id"))
    part_id = Column(Integer, ForeignKey("parts.id"))
    quantity = Column(Integer, default=0)
    min_stock = Column(Integer, default=5)
    max_stock = Column(Integer, default=100)
    location = Column(String(50))
    last_restock_date = Column(DateTime)
    
    __table_args__ = (
        UniqueConstraint('agent_id', 'part_id', name='unique_agent_part'),
    )

class Order(Base):
    __tablename__ = "orders"
    id = Column(Integer, primary_key=True)
    order_number = Column(String(20), unique=True)
    agent_id = Column(Integer, ForeignKey("agents.id"))
    customer_name = Column(String(100))
    customer_email = Column(String(100))
    customer_phone = Column(String(20))
    status = Column(Enum(OrderStatus), default=OrderStatus.PENDING)
    total_amount = Column(Float)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, onupdate=datetime.utcnow)
    
    items = relationship("OrderItem", back_populates="order")
    
    @hybrid_property
    def is_complete(self):
        return self.status in (OrderStatus.DELIVERED, OrderStatus.CANCELLED)

# Association Tables
agent_specializations = Table(
    'agent_specializations',
    Base.metadata,
    Column('agent_id', Integer, ForeignKey('agents.id')),
    Column('category', Enum(PartCategory))
)

class OrderItem(Base):
    __tablename__ = "order_items"
    id = Column(Integer, primary_key=True, index=True)
    order_id = Column(Integer, ForeignKey("orders.id"))
    part_id = Column(Integer, ForeignKey("parts.id"))
    quantity = Column(Integer)
    price = Column(Float)
    order = relationship("Order", back_populates="items")
    part = relationship("Part")
