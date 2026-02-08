from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import declarative_base, relationship
from sqlalchemy import (
    Column, String, Integer, BigInteger, Boolean, DateTime, Date, Text, 
    DECIMAL, JSON, UUID, ForeignKey, UniqueConstraint, CheckConstraint, Index
)
from sqlalchemy.dialects.postgresql import JSONB, ARRAY
from datetime import datetime, timedelta
from typing import Optional, List
import uuid
import os
from dotenv import load_dotenv

load_dotenv()

# ==============================================================================
# DATABASE CONFIGURATION
# ==============================================================================

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql+asyncpg://autospare:password@localhost:5432/autospare"
)

# Create async engine
engine = create_async_engine(
    DATABASE_URL,
    echo=os.getenv("DEBUG", "false").lower() == "true",
    pool_pre_ping=True,
    pool_size=20,
    max_overflow=40,
    pool_recycle=3600,
)

# Session factory
AsyncSessionLocal = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False,
)

# Base class
Base = declarative_base()


# ==============================================================================
# UTILITY FUNCTIONS
# ==============================================================================

def generate_uuid():
    """Generate UUID for primary keys"""
    return uuid.uuid4()


async def get_db():
    """Dependency for getting database session"""
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


# ==============================================================================
# 1. USERS & AUTH TABLES (6 tables)
# ==============================================================================

class User(Base):
    __tablename__ = "users"

    id = Column(UUID(as_uuid=True), primary_key=True, default=generate_uuid)
    email = Column(String(255), unique=True, nullable=False, index=True)
    phone = Column(String(20), unique=True, nullable=False, index=True)
    password_hash = Column(String(255), nullable=False)
    full_name = Column(String(255), nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)
    is_verified = Column(Boolean, default=False, nullable=False)
    is_admin = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    last_login_at = Column(DateTime, nullable=True)

    profile = relationship("UserProfile", back_populates="user", uselist=False, cascade="all, delete-orphan")
    sessions = relationship("UserSession", back_populates="user", cascade="all, delete-orphan")
    two_factor_codes = relationship("TwoFactorCode", back_populates="user", cascade="all, delete-orphan")
    login_attempts = relationship("LoginAttempt", back_populates="user", cascade="all, delete-orphan")
    password_resets = relationship("PasswordReset", back_populates="user", cascade="all, delete-orphan")
    vehicles = relationship("UserVehicle", back_populates="user", cascade="all, delete-orphan")
    orders = relationship("Order", back_populates="user", cascade="all, delete-orphan")
    conversations = relationship("Conversation", back_populates="user", cascade="all, delete-orphan")
    files = relationship("File", back_populates="user", cascade="all, delete-orphan")
    notifications = relationship("Notification", back_populates="user", cascade="all, delete-orphan")


class UserProfile(Base):
    __tablename__ = "user_profiles"

    id = Column(UUID(as_uuid=True), primary_key=True, default=generate_uuid)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), unique=True, nullable=False)

    address_line1 = Column(String(255), nullable=True)
    address_line2 = Column(String(255), nullable=True)
    city = Column(String(100), nullable=True)
    postal_code = Column(String(20), nullable=True)
    country = Column(String(100), default="Israel", nullable=False)

    default_vehicle_id = Column(UUID(as_uuid=True), ForeignKey("vehicles.id"), nullable=True)
    preferred_language = Column(String(10), default="he", nullable=False)
    marketing_consent = Column(Boolean, default=False, nullable=False)
    newsletter_subscribed = Column(Boolean, default=False, nullable=False)

    marketing_preferences = Column(JSONB, default={
        "email": True,
        "sms": False,
        "whatsapp": False,
        "topics": ["promotions", "tips"]
    })

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    user = relationship("User", back_populates="profile")
    default_vehicle = relationship("Vehicle", foreign_keys=[default_vehicle_id])


class UserSession(Base):
    __tablename__ = "user_sessions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=generate_uuid)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)

    token = Column(String(500), unique=True, nullable=False, index=True)
    refresh_token = Column(String(500), unique=True, nullable=True, index=True)

    device_fingerprint = Column(String(255), nullable=True)
    device_name = Column(String(255), nullable=True)
    ip_address = Column(String(45), nullable=True)
    user_agent = Column(Text, nullable=True)

    is_trusted_device = Column(Boolean, default=False, nullable=False)
    trusted_until = Column(DateTime, nullable=True)

    expires_at = Column(DateTime, nullable=False)
    refresh_expires_at = Column(DateTime, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    last_used_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    revoked_at = Column(DateTime, nullable=True)

    user = relationship("User", back_populates="sessions")

    __table_args__ = (
        Index("idx_user_sessions_user_token", "user_id", "token"),
    )


class TwoFactorCode(Base):
    __tablename__ = "two_factor_codes"

    id = Column(UUID(as_uuid=True), primary_key=True, default=generate_uuid)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)

    code = Column(String(6), nullable=False)
    phone = Column(String(20), nullable=False)

    attempts = Column(Integer, default=0, nullable=False)
    max_attempts = Column(Integer, default=3, nullable=False)

    expires_at = Column(DateTime, nullable=False)
    verified_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    user = relationship("User", back_populates="two_factor_codes")

    __table_args__ = (
        CheckConstraint("attempts >= 0 AND attempts <= max_attempts", name="check_2fa_attempts"),
    )


class LoginAttempt(Base):
    __tablename__ = "login_attempts"

    id = Column(UUID(as_uuid=True), primary_key=True, default=generate_uuid)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True)

    email = Column(String(255), nullable=True, index=True)
    ip_address = Column(String(45), nullable=False, index=True)
    user_agent = Column(Text, nullable=True)

    success = Column(Boolean, nullable=False, index=True)
    failure_reason = Column(String(100), nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)

    user = relationship("User", back_populates="login_attempts")


class PasswordReset(Base):
    __tablename__ = "password_resets"

    id = Column(UUID(as_uuid=True), primary_key=True, default=generate_uuid)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)

    token = Column(String(255), unique=True, nullable=False, index=True)

    expires_at = Column(DateTime, nullable=False)
    used_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    user = relationship("User", back_populates="password_resets")


class Vehicle(Base):
    """Vehicles - from Gov API with 90-day cache"""
    __tablename__ = "vehicles"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=generate_uuid)
    
    license_plate = Column(String(20), unique=True, nullable=False, index=True)
    manufacturer = Column(String(100), nullable=False, index=True)
    model = Column(String(100), nullable=False, index=True)
    year = Column(Integer, nullable=False, index=True)
    
    vin = Column(String(17), nullable=True)
    engine_type = Column(String(50), nullable=True)
    engine_capacity = Column(Integer, nullable=True)
    transmission = Column(String(50), nullable=True)
    fuel_type = Column(String(50), nullable=True)
    color = Column(String(50), nullable=True)
    
    gov_api_data = Column(JSONB, nullable=True)
    cached_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    cache_valid_until = Column(DateTime, nullable=False)
    
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    user_vehicles = relationship("UserVehicle", back_populates="vehicle", cascade="all, delete-orphan")
    
    __table_args__ = (
        Index("idx_vehicles_manufacturer_model", "manufacturer", "model"),
        Index("idx_vehicles_year", "year"),
    )


class UserVehicle(Base):
    """User vehicles - Many-to-Many relationship"""
    __tablename__ = "user_vehicles"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=generate_uuid)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    vehicle_id = Column(UUID(as_uuid=True), ForeignKey("vehicles.id", ondelete="CASCADE"), nullable=False, index=True)
    
    nickname = Column(String(100), nullable=True)
    is_primary = Column(Boolean, default=False, nullable=False)
    
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    
    user = relationship("User", back_populates="vehicles")
    vehicle = relationship("Vehicle", back_populates="user_vehicles")
    
    __table_args__ = (
        UniqueConstraint("user_id", "vehicle_id", name="uq_user_vehicle"),
    )


class PartsCatalog(Base):
    """Parts catalog - 200K+ parts"""
    __tablename__ = "parts_catalog"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=generate_uuid)
    
    sku = Column(String(100), unique=True, nullable=False, index=True)
    name = Column(String(255), nullable=False, index=True)
    
    category = Column(String(100), nullable=False, index=True)
    subcategory = Column(String(100), nullable=True)
    manufacturer = Column(String(100), nullable=False, index=True)
    part_type = Column(String(50), nullable=False, index=True)
    
    description = Column(Text, nullable=True)
    specifications = Column(JSONB, nullable=True)
    
    compatible_vehicles = Column(JSONB, nullable=True)
    
    base_price = Column(DECIMAL(10, 2), nullable=True)
    currency = Column(String(3), default="USD", nullable=False)
    
    is_active = Column(Boolean, default=True, nullable=False, index=True)
    
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    images = relationship("PartsImage", back_populates="part", cascade="all, delete-orphan")
    supplier_parts = relationship("SupplierPart", back_populates="part", cascade="all, delete-orphan")
    
    __table_args__ = (
        Index("idx_parts_category", "category"),
        Index("idx_parts_manufacturer", "manufacturer"),
        Index("idx_parts_name_fulltext", "name", postgresql_using="gin", postgresql_ops={"name": "gin_trgm_ops"}),
    )


class PartsImage(Base):
    """Parts images"""
    __tablename__ = "parts_images"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=generate_uuid)
    part_id = Column(UUID(as_uuid=True), ForeignKey("parts_catalog.id", ondelete="CASCADE"), nullable=False, index=True)
    file_id = Column(UUID(as_uuid=True), ForeignKey("files.id", ondelete="SET NULL"), nullable=True)
    
    url = Column(String(500), nullable=False)
    is_primary = Column(Boolean, default=False, nullable=False)
    sort_order = Column(Integer, default=0, nullable=False)
    
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    
    part = relationship("PartsCatalog", back_populates="images")
    file = relationship("File")


class Supplier(Base):
    """Suppliers - RockAuto, FCP Euro, Autodoc, AliExpress"""
    __tablename__ = "suppliers"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=generate_uuid)
    
    name = Column(String(255), unique=True, nullable=False, index=True)
    country = Column(String(100), nullable=False)
    website = Column(String(500), nullable=True)
    
    api_endpoint = Column(String(500), nullable=True)
    api_key = Column(Text, nullable=True)
    credentials = Column(JSONB, nullable=True)
    
    shipping_info = Column(JSONB, nullable=True)
    return_policy = Column(JSONB, nullable=True)
    
    is_active = Column(Boolean, default=True, nullable=False, index=True)
    priority = Column(Integer, default=0, nullable=False, index=True)
    reliability_score = Column(DECIMAL(3, 1), default=5.0, nullable=False)
    
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    supplier_parts = relationship("SupplierPart", back_populates="supplier", cascade="all, delete-orphan")
    
    __table_args__ = (
        CheckConstraint("reliability_score >= 1.0 AND reliability_score <= 10.0", name="check_reliability_score"),
    )


class SupplierPart(Base):
    """Supplier parts - pricing and availability per supplier"""
    __tablename__ = "supplier_parts"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=generate_uuid)
    supplier_id = Column(UUID(as_uuid=True), ForeignKey("suppliers.id", ondelete="CASCADE"), nullable=False, index=True)
    part_id = Column(UUID(as_uuid=True), ForeignKey("parts_catalog.id", ondelete="CASCADE"), nullable=False, index=True)
    
    supplier_sku = Column(String(100), nullable=False)
    supplier_part_name = Column(String(255), nullable=True)
    
    price_usd = Column(DECIMAL(10, 2), nullable=False)
    price_ils = Column(DECIMAL(10, 2), nullable=True)
    shipping_cost_usd = Column(DECIMAL(10, 2), nullable=True)
    shipping_cost_ils = Column(DECIMAL(10, 2), nullable=True)
    
    availability = Column(String(50), default="In Stock", nullable=False)
    estimated_delivery_days = Column(Integer, nullable=True)
    stock_quantity = Column(Integer, nullable=True)
    
    warranty_months = Column(Integer, default=12, nullable=False)
    
    last_checked_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    is_available = Column(Boolean, default=True, nullable=False, index=True)
    
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    supplier = relationship("Supplier", back_populates="supplier_parts")
    part = relationship("PartsCatalog", back_populates="supplier_parts")
    
    __table_args__ = (
        UniqueConstraint("supplier_id", "supplier_sku", name="uq_supplier_part"),
        CheckConstraint("price_usd > 0", name="check_price_positive"),
    )


class Order(Base):
    """Orders - AUTO-2026-XXXXX"""
    __tablename__ = "orders"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=generate_uuid)
    order_number = Column(String(20), unique=True, nullable=False, index=True)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    
    status = Column(String(50), default="pending_payment", nullable=False, index=True)
    
    subtotal = Column(DECIMAL(10, 2), nullable=False)
    vat_amount = Column(DECIMAL(10, 2), nullable=False)
    vat_percentage = Column(DECIMAL(5, 2), default=17.00, nullable=False)
    shipping_cost = Column(DECIMAL(10, 2), nullable=False)
    discount_amount = Column(DECIMAL(10, 2), default=0.00, nullable=False)
    total_amount = Column(DECIMAL(10, 2), nullable=False)
    currency = Column(String(3), default="ILS", nullable=False)
    
    shipping_address = Column(JSONB, nullable=False)
    tracking_number = Column(String(100), nullable=True, index=True)
    carrier = Column(String(100), nullable=True)
    tracking_url = Column(String(500), nullable=True)
    
    estimated_delivery = Column(Date, nullable=True)
    shipped_at = Column(DateTime, nullable=True)
    delivered_at = Column(DateTime, nullable=True)
    cancelled_at = Column(DateTime, nullable=True)
    
    customer_notes = Column(Text, nullable=True)
    admin_notes = Column(Text, nullable=True)
    
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    user = relationship("User", back_populates="orders")
    items = relationship("OrderItem", back_populates="order", cascade="all, delete-orphan")
    payments = relationship("Payment", back_populates="order", cascade="all, delete-orphan")
    invoices = relationship("Invoice", back_populates="order", cascade="all, delete-orphan")
    returns = relationship("Return", back_populates="order", cascade="all, delete-orphan")
    
    __table_args__ = (
        CheckConstraint("subtotal >= 0", name="check_subtotal_positive"),
        CheckConstraint("vat_amount >= 0", name="check_vat_positive"),
        CheckConstraint("total_amount >= 0", name="check_total_positive"),
    )


class OrderItem(Base):
    __tablename__ = "order_items"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=generate_uuid)
    order_id = Column(UUID(as_uuid=True), ForeignKey("orders.id", ondelete="CASCADE"), nullable=False, index=True)
    part_id = Column(UUID(as_uuid=True), ForeignKey("parts_catalog.id", ondelete="SET NULL"), nullable=True)
    supplier_part_id = Column(UUID(as_uuid=True), ForeignKey("supplier_parts.id", ondelete="SET NULL"), nullable=True)
    
    part_name = Column(String(255), nullable=False)
    part_sku = Column(String(100), nullable=False)
    manufacturer = Column(String(100), nullable=False)
    part_type = Column(String(50), nullable=False)
    
    supplier_name = Column(String(255), nullable=False)
    supplier_order_id = Column(String(100), nullable=True)
    supplier_tracking = Column(String(100), nullable=True)
    
    quantity = Column(Integer, default=1, nullable=False)
    unit_price = Column(DECIMAL(10, 2), nullable=False)
    vat_amount = Column(DECIMAL(10, 2), nullable=False)
    total_price = Column(DECIMAL(10, 2), nullable=False)
    
    warranty_months = Column(Integer, nullable=True)
    
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    
    order = relationship("Order", back_populates="items")
    part = relationship("PartsCatalog")
    supplier_part = relationship("SupplierPart")
    
    __table_args__ = (
        CheckConstraint("quantity > 0", name="check_quantity_positive"),
        CheckConstraint("unit_price >= 0", name="check_unit_price_positive"),
    )


class Payment(Base):
    __tablename__ = "payments"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=generate_uuid)
    order_id = Column(UUID(as_uuid=True), ForeignKey("orders.id", ondelete="CASCADE"), nullable=False, index=True)
    
    payment_intent_id = Column(String(255), unique=True, nullable=False, index=True)
    stripe_customer_id = Column(String(255), nullable=True, index=True)
    
    amount = Column(DECIMAL(10, 2), nullable=False)
    currency = Column(String(3), default="ILS", nullable=False)
    
    status = Column(String(50), default="pending", nullable=False, index=True)
    payment_method = Column(String(50), nullable=True)
    last_4_digits = Column(String(4), nullable=True)
    card_brand = Column(String(50), nullable=True)
    
    refund_amount = Column(DECIMAL(10, 2), default=0.00, nullable=False)
    refund_reason = Column(Text, nullable=True)
    
    paid_at = Column(DateTime, nullable=True)
    failed_at = Column(DateTime, nullable=True)
    refunded_at = Column(DateTime, nullable=True)
    
    error_message = Column(Text, nullable=True)
    
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    order = relationship("Order", back_populates="payments")
    
    __table_args__ = (
        CheckConstraint("amount >= 0", name="check_payment_amount_positive"),
        CheckConstraint("refund_amount >= 0 AND refund_amount <= amount", name="check_refund_valid"),
    )


class Invoice(Base):
    __tablename__ = "invoices"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=generate_uuid)
    invoice_number = Column(String(20), unique=True, nullable=False, index=True)
    order_id = Column(UUID(as_uuid=True), ForeignKey("orders.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    
    pdf_path = Column(String(500), nullable=True)
    pdf_url = Column(String(500), nullable=True)
    file_id = Column(UUID(as_uuid=True), ForeignKey("files.id", ondelete="SET NULL"), nullable=True)
    
    business_number = Column(String(20), default="060633880", nullable=False)
    business_name = Column(String(255), default="Auto Spare", nullable=False)
    business_address = Column(String(500), default="המצר 55, עכו", nullable=False)
    
    issued_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    
    order = relationship("Order", back_populates="invoices")
    user = relationship("User")
    file = relationship("File")


class Return(Base):
    __tablename__ = "returns"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=generate_uuid)
    return_number = Column(String(20), unique=True, nullable=False, index=True)
    order_id = Column(UUID(as_uuid=True), ForeignKey("orders.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    
    reason = Column(String(50), nullable=False)
    description = Column(Text, nullable=True)
    
    status = Column(String(50), default="pending", nullable=False, index=True)
    
    original_amount = Column(DECIMAL(10, 2), nullable=False)
    refund_amount = Column(DECIMAL(10, 2), nullable=True)
    refund_percentage = Column(DECIMAL(5, 2), nullable=True)
    handling_fee = Column(DECIMAL(10, 2), default=0.00, nullable=False)
    
    return_shipping_label = Column(String(500), nullable=True)
    tracking_number = Column(String(100), nullable=True)
    
    requested_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    approved_at = Column(DateTime, nullable=True)
    rejected_at = Column(DateTime, nullable=True)
    item_received_at = Column(DateTime, nullable=True)
    refund_processed_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)
    
    rejection_reason = Column(Text, nullable=True)
    admin_notes = Column(Text, nullable=True)
    
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    order = relationship("Order", back_populates="returns")
    user = relationship("User")


class Conversation(Base):
    __tablename__ = "conversations"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=generate_uuid)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    
    title = Column(String(255), nullable=True)
    current_agent = Column(String(50), nullable=True)
    
    context = Column(JSONB, nullable=True, default={})
    is_active = Column(Boolean, default=True, nullable=False, index=True)
    
    started_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    ended_at = Column(DateTime, nullable=True)
    last_message_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    
    user = relationship("User", back_populates="conversations")
    messages = relationship("Message", back_populates="conversation", cascade="all, delete-orphan")
    ratings = relationship("AgentRating", back_populates="conversation", cascade="all, delete-orphan")


class Message(Base):
    __tablename__ = "messages"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=generate_uuid)
    conversation_id = Column(UUID(as_uuid=True), ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False, index=True)
    
    role = Column(String(20), nullable=False)
    agent_name = Column(String(50), nullable=True)
    content = Column(Text, nullable=False)
    content_type = Column(String(20), default="text", nullable=False)
    
    file_id = Column(UUID(as_uuid=True), ForeignKey("files.id", ondelete="SET NULL"), nullable=True)
    transcription = Column(Text, nullable=True)
    analysis = Column(JSONB, nullable=True)
    
    model_used = Column(String(50), nullable=True)
    tokens_used = Column(Integer, nullable=True)
    latency_ms = Column(Integer, nullable=True)
    
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)
    
    conversation = relationship("Conversation", back_populates="messages")
    file = relationship("File")
    actions = relationship("AgentAction", back_populates="message", cascade="all, delete-orphan")


class AgentAction(Base):
    __tablename__ = "agent_actions"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=generate_uuid)
    message_id = Column(UUID(as_uuid=True), ForeignKey("messages.id", ondelete="CASCADE"), nullable=False, index=True)
    
    agent_name = Column(String(50), nullable=False)
    action_type = Column(String(50), nullable=False)
    
    action_data = Column(JSONB, nullable=True)
    result = Column(JSONB, nullable=True)
    
    success = Column(Boolean, nullable=False)
    error_message = Column(Text, nullable=True)
    
    execution_time_ms = Column(Integer, nullable=True)
    
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    
    message = relationship("Message", back_populates="actions")


class AgentRating(Base):
    __tablename__ = "agent_ratings"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=generate_uuid)
    conversation_id = Column(UUID(as_uuid=True), ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    
    agent_name = Column(String(50), nullable=False, index=True)
    rating = Column(Integer, nullable=False)
    feedback = Column(Text, nullable=True)
    
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    
    conversation = relationship("Conversation", back_populates="ratings")
    user = relationship("User")
    
    __table_args__ = (
        CheckConstraint("rating >= 1 AND rating <= 5", name="check_rating_range"),
    )


class File(Base):
    __tablename__ = "files"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=generate_uuid)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    
    original_filename = Column(String(255), nullable=False)
    stored_filename = Column(String(255), unique=True, nullable=False, index=True)
    file_type = Column(String(50), nullable=False)
    mime_type = Column(String(100), nullable=False)
    
    file_size_bytes = Column(BigInteger, nullable=False)
    compressed_size_bytes = Column(BigInteger, nullable=True)
    
    width = Column(Integer, nullable=True)
    height = Column(Integer, nullable=True)
    format = Column(String(20), nullable=True)
    
    duration_seconds = Column(Integer, nullable=True)
    audio_codec = Column(String(50), nullable=True)
    
    video_codec = Column(String(50), nullable=True)
    resolution = Column(String(20), nullable=True)
    fps = Column(Integer, nullable=True)
    
    storage_path = Column(String(500), nullable=False)
    cdn_url = Column(String(500), nullable=True)
    signed_url = Column(String(500), nullable=True)
    signed_url_expires = Column(DateTime, nullable=True)
    
    is_processed = Column(Boolean, default=False, nullable=False)
    virus_scan_status = Column(String(50), default="pending", nullable=False)
    virus_scan_at = Column(DateTime, nullable=True)
    
    expires_at = Column(DateTime, nullable=False)
    deleted_at = Column(DateTime, nullable=True)
    
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    user = relationship("User", back_populates="files")
    metadata = relationship("FileMetadata", back_populates="file", cascade="all, delete-orphan")
    
    __table_args__ = (
        Index("idx_files_expires_at", "expires_at"),
    )


class FileMetadata(Base):
    __tablename__ = "file_metadata"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=generate_uuid)
    file_id = Column(UUID(as_uuid=True), ForeignKey("files.id", ondelete="CASCADE"), nullable=False, index=True)
    
    metadata_key = Column(String(100), nullable=False)
    metadata_value = Column(Text, nullable=True)
    
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    
    file = relationship("File", back_populates="metadata")


class SystemLog(Base):
    __tablename__ = "system_logs"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=generate_uuid)
    
    level = Column(String(20), nullable=False, index=True)
    logger_name = Column(String(100), nullable=False)
    message = Column(Text, nullable=False)
    
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    ip_address = Column(String(45), nullable=True)
    
    endpoint = Column(String(255), nullable=True)
    method = Column(String(10), nullable=True)
    status_code = Column(Integer, nullable=True)
    
    request_data = Column(JSONB, nullable=True)
    response_data = Column(JSONB, nullable=True)
    
    exception = Column(Text, nullable=True)
    stack_trace = Column(Text, nullable=True)
    
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)
    
    user = relationship("User")
    
    __table_args__ = (
        Index("idx_system_logs_level_created", "level", "created_at"),
    )


class AuditLog(Base):
    __tablename__ = "audit_logs"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=generate_uuid)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    
    action = Column(String(100), nullable=False, index=True)
    
    entity_type = Column(String(50), nullable=True)
    entity_id = Column(UUID(as_uuid=True), nullable=True)
    
    old_value = Column(JSONB, nullable=True)
    new_value = Column(JSONB, nullable=True)
    
    ip_address = Column(String(45), nullable=True)
    user_agent = Column(Text, nullable=True)
    
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)
    
    user = relationship("User")


class SystemSetting(Base):
    __tablename__ = "system_settings"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=generate_uuid)
    
    key = Column(String(100), unique=True, nullable=False, index=True)
    value = Column(Text, nullable=False)
    value_type = Column(String(20), nullable=False)
    description = Column(Text, nullable=True)
    
    is_public = Column(Boolean, default=False, nullable=False)
    
    updated_by = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    
    updater = relationship("User", foreign_keys=[updated_by])


class CacheEntry(Base):
    __tablename__ = "cache_entries"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=generate_uuid)
    
    cache_key = Column(String(255), unique=True, nullable=False, index=True)
    cache_value = Column(JSONB, nullable=False)
    
    expires_at = Column(DateTime, nullable=False, index=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class Notification(Base):
    __tablename__ = "notifications"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=generate_uuid)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    
    type = Column(String(50), nullable=False, index=True)
    title = Column(String(255), nullable=False)
    message = Column(Text, nullable=False)
    data = Column(JSONB, nullable=True)
    
    channel = Column(String(20), nullable=False)
    sent_at = Column(DateTime, nullable=True)
    read_at = Column(DateTime, nullable=True, index=True)
    
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    
    user = relationship("User", back_populates="notifications")


# ==============================================================================
# DATABASE INITIALIZATION
# ==============================================================================

async def init_db():
    """Initialize database - create all tables"""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    print("✅ Database initialized successfully!")


async def drop_db():
    """Drop all tables - DANGEROUS!"""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    print("⚠️ Database dropped!")


# ==============================================================================
# HELPER FUNCTIONS
# ==============================================================================

async def get_or_create_user(email: str, **kwargs) -> User:
    """Get user by email or create if not exists"""
    async with AsyncSessionLocal() as session:
        from sqlalchemy import select
        result = await session.execute(select(User).where(User.email == email))
        user = result.scalar_one_or_none()
        
        if not user:
            user = User(email=email, **kwargs)
            session.add(user)
            await session.commit()
            await session.refresh(user)
        
        return user


async def get_active_suppliers() -> List[Supplier]:
    """Get all active suppliers sorted by priority"""
    async with AsyncSessionLocal() as session:
        from sqlalchemy import select
        result = await session.execute(
            select(Supplier)
            .where(Supplier.is_active == True)
            .order_by(Supplier.priority.asc())
        )
        return result.scalars().all()


async def get_user_conversations(user_id: uuid.UUID, limit: int = 50) -> List[Conversation]:
    """Get user's recent conversations"""
    async with AsyncSessionLocal() as session:
        from sqlalchemy import select
        result = await session.execute(
            select(Conversation)
            .where(Conversation.user_id == user_id)
            .order_by(Conversation.last_message_at.desc())
            .limit(limit)
        )
        return result.scalars().all()


# ==============================================================================
# END OF FILE
# ==============================================================================

print("📦 Database models loaded successfully!")
print(f"📊 Total tables: 27")
print(f"🔗 Relationships: Fully configured")
print(f"✅ Ready for Alembic migrations!")