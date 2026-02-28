"""
==============================================================================
AUTO SPARE - DATABASE MODELS (SQLAlchemy 2.0 Async)
==============================================================================
28 Tables:
  Users & Auth (6): users, user_profiles, user_sessions,
                    two_factor_codes, login_attempts, password_resets
  Vehicles & Parts (4): vehicles, user_vehicles, parts_catalog, parts_images
  Suppliers (2): suppliers, supplier_parts
  Orders & Payments (5): orders, order_items, payments, invoices, returns
  AI & Chat (4): conversations, messages, agent_actions, agent_ratings
  Files & Media (2): files, file_metadata
  System & Logs (5): system_logs, audit_logs, system_settings,
                     cache_entries, notifications
==============================================================================
"""

import os
import uuid
from datetime import datetime, timedelta
from typing import AsyncGenerator, Optional

from dotenv import load_dotenv
from sqlalchemy import (
    Boolean, Column, DateTime, ForeignKey, Integer, Numeric,
    String, Text, BigInteger, JSON, UniqueConstraint, CheckConstraint,
    Index, func,
)
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import DeclarativeBase, relationship, sessionmaker

load_dotenv()

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql+asyncpg://autospare:autospare@localhost:5432/autospare"
)

engine = create_async_engine(DATABASE_URL, echo=False, future=True)
async_session_factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """Dependency: yields an async database session."""
    async with async_session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


# ==============================================================================
# 1. USERS & AUTH TABLES (6)
# ==============================================================================

class User(Base):
    __tablename__ = "users"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email = Column(String(255), unique=True, nullable=False, index=True)
    phone = Column(String(20), unique=True, nullable=False)           # encrypted
    password_hash = Column(String(255), nullable=False)
    full_name = Column(String(255), nullable=False)
    role = Column(String(50), nullable=False, default="customer")     # customer / admin
    is_active = Column(Boolean, default=True, nullable=False)
    is_verified = Column(Boolean, default=False, nullable=False)
    is_admin = Column(Boolean, default=False, nullable=False)
    failed_login_count = Column(Integer, default=0, nullable=False)
    locked_until = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    profile = relationship("UserProfile", back_populates="user", uselist=False, cascade="all, delete-orphan")
    sessions = relationship("UserSession", back_populates="user", cascade="all, delete-orphan")
    two_factor_codes = relationship("TwoFactorCode", back_populates="user", cascade="all, delete-orphan")
    login_attempts = relationship("LoginAttempt", back_populates="user")
    password_resets = relationship("PasswordReset", back_populates="user", cascade="all, delete-orphan")
    user_vehicles = relationship("UserVehicle", back_populates="user", cascade="all, delete-orphan")
    orders = relationship("Order", back_populates="user")
    invoices = relationship("Invoice", back_populates="user")
    returns = relationship("Return", back_populates="user")
    conversations = relationship("Conversation", back_populates="user")
    files = relationship("File", back_populates="user")
    notifications = relationship("Notification", back_populates="user")
    agent_ratings = relationship("AgentRating", back_populates="user")


class UserProfile(Base):
    __tablename__ = "user_profiles"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), unique=True, nullable=False)
    address_line1 = Column(String(255))                               # encrypted
    address_line2 = Column(String(255))                               # encrypted
    city = Column(String(100))
    postal_code = Column(String(20))
    default_vehicle_id = Column(UUID(as_uuid=True), ForeignKey("vehicles.id"), nullable=True)
    marketing_consent = Column(Boolean, default=False)
    newsletter_subscribed = Column(Boolean, default=False)
    marketing_preferences = Column(JSONB, default=dict)
    preferred_language = Column(String(10), default="he")
    avatar_url = Column(String(500), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    user = relationship("User", back_populates="profile")


class UserSession(Base):
    __tablename__ = "user_sessions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    token = Column(String(500), unique=True, nullable=False)
    refresh_token = Column(String(500), unique=True, nullable=True)
    device_fingerprint = Column(String(255))
    device_name = Column(String(255))
    ip_address = Column(String(45))
    user_agent = Column(Text)
    is_trusted_device = Column(Boolean, default=False)
    trusted_until = Column(DateTime, nullable=True)
    expires_at = Column(DateTime, nullable=False)
    last_used_at = Column(DateTime, default=datetime.utcnow)
    revoked_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    # Relationships
    user = relationship("User", back_populates="sessions")


class TwoFactorCode(Base):
    __tablename__ = "two_factor_codes"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    code = Column(String(6), nullable=False)
    phone = Column(String(20))
    attempts = Column(Integer, default=0)
    expires_at = Column(DateTime, nullable=False)
    verified_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    # Relationships
    user = relationship("User", back_populates="two_factor_codes")


class LoginAttempt(Base):
    __tablename__ = "login_attempts"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    email = Column(String(255), nullable=True)
    ip_address = Column(String(45), nullable=False, index=True)
    success = Column(Boolean, nullable=False)
    failure_reason = Column(String(100), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)

    # Relationships
    user = relationship("User", back_populates="login_attempts")


class PasswordReset(Base):
    __tablename__ = "password_resets"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    token = Column(String(255), unique=True, nullable=False)
    expires_at = Column(DateTime, nullable=False)
    used_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    # Relationships
    user = relationship("User", back_populates="password_resets")


# ==============================================================================
# 2. VEHICLES & PARTS TABLES (4)
# ==============================================================================

class Vehicle(Base):
    __tablename__ = "vehicles"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    license_plate = Column(String(20), unique=True, nullable=True)   # encrypted
    manufacturer = Column(String(100), nullable=False, index=True)
    model = Column(String(100), nullable=False)
    year = Column(Integer, nullable=False)
    vin = Column(String(17), nullable=True)                           # encrypted
    engine_type = Column(String(50))
    transmission = Column(String(50))
    fuel_type = Column(String(50))
    gov_api_data = Column(JSONB, default=dict)                        # cache from transport ministry API
    cached_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        Index("idx_vehicles_manufacturer_model", "manufacturer", "model"),
    )

    # Relationships
    user_vehicles = relationship("UserVehicle", back_populates="vehicle")
    profiles_default = relationship("UserProfile", foreign_keys="UserProfile.default_vehicle_id")


class UserVehicle(Base):
    __tablename__ = "user_vehicles"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    vehicle_id = Column(UUID(as_uuid=True), ForeignKey("vehicles.id", ondelete="CASCADE"), nullable=False)
    nickname = Column(String(100), nullable=True)
    is_primary = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("user_id", "vehicle_id"),
        Index("idx_user_vehicles_user_id", "user_id"),
    )

    # Relationships
    user = relationship("User", back_populates="user_vehicles")
    vehicle = relationship("Vehicle", back_populates="user_vehicles")


class PartsCatalog(Base):
    __tablename__ = "parts_catalog"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    sku = Column(String(100), unique=True, nullable=False, index=True)
    name = Column(String(255), nullable=False, index=True)
    category = Column(String(100), index=True)                       # בלמים, מנוע...
    manufacturer = Column(String(100), index=True)                   # Bosch, Brembo...
    part_type = Column(String(50))                                   # OEM, Original, Aftermarket
    description = Column(Text)
    specifications = Column(JSONB, default=dict)
    compatible_vehicles = Column(JSONB, default=list)
    base_price = Column(Numeric(10, 2), nullable=True)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    images = relationship("PartImage", back_populates="part", cascade="all, delete-orphan")
    supplier_parts = relationship("SupplierPart", back_populates="part")
    order_items = relationship("OrderItem", back_populates="part")


class PartImage(Base):
    __tablename__ = "parts_images"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    part_id = Column(UUID(as_uuid=True), ForeignKey("parts_catalog.id", ondelete="CASCADE"), nullable=False, index=True)
    file_id = Column(UUID(as_uuid=True), ForeignKey("files.id"), nullable=True)
    url = Column(String(500))
    is_primary = Column(Boolean, default=False)
    sort_order = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.utcnow)

    # Relationships
    part = relationship("PartsCatalog", back_populates="images")


# ==============================================================================
# 3. SUPPLIERS TABLES (2)
# ==============================================================================

class Supplier(Base):
    __tablename__ = "suppliers"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String(255), unique=True, nullable=False)          # RockAuto, FCP Euro...
    country = Column(String(100))
    website = Column(String(500))
    api_endpoint = Column(String(500))
    api_key = Column(Text, nullable=True)                            # encrypted
    credentials = Column(JSONB, default=dict)                        # encrypted
    shipping_info = Column(JSONB, default=dict)
    return_policy = Column(JSONB, default=dict)
    reliability_score = Column(Numeric(3, 2), default=5.0)
    is_active = Column(Boolean, default=True)
    priority = Column(Integer, default=0)                            # lower = higher priority
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    supplier_parts = relationship("SupplierPart", back_populates="supplier")


class SupplierPart(Base):
    __tablename__ = "supplier_parts"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    supplier_id = Column(UUID(as_uuid=True), ForeignKey("suppliers.id", ondelete="CASCADE"), nullable=False, index=True)
    part_id = Column(UUID(as_uuid=True), ForeignKey("parts_catalog.id", ondelete="CASCADE"), nullable=False, index=True)
    supplier_sku = Column(String(100), nullable=True)
    price_usd = Column(Numeric(10, 2), nullable=False)
    price_ils = Column(Numeric(10, 2), nullable=True)
    shipping_cost_usd = Column(Numeric(10, 2), nullable=True)
    shipping_cost_ils = Column(Numeric(10, 2), nullable=True)
    availability = Column(String(50), default="In Stock")
    warranty_months = Column(Integer, default=12)
    estimated_delivery_days = Column(Integer, default=14)
    last_checked_at = Column(DateTime, nullable=True)
    is_available = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("supplier_id", "supplier_sku"),
    )

    # Relationships
    supplier = relationship("Supplier", back_populates="supplier_parts")
    part = relationship("PartsCatalog", back_populates="supplier_parts")
    order_items = relationship("OrderItem", back_populates="supplier_part")


# ==============================================================================
# 4. ORDERS & PAYMENTS TABLES (5)
# ==============================================================================

class Order(Base):
    __tablename__ = "orders"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    order_number = Column(String(20), unique=True, nullable=False, index=True)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False, index=True)
    status = Column(String(50), nullable=False, default="pending_payment", index=True)
    # statuses: pending_payment, paid, processing, supplier_ordered, shipped,
    #           delivered, cancelled, refunded
    subtotal = Column(Numeric(10, 2), nullable=False)                # without VAT
    vat_amount = Column(Numeric(10, 2), nullable=False)              # 17%
    shipping_cost = Column(Numeric(10, 2), nullable=False)
    discount_amount = Column(Numeric(10, 2), default=0)
    total_amount = Column(Numeric(10, 2), nullable=False)
    shipping_address = Column(JSONB, nullable=False)                 # encrypted
    tracking_number = Column(String(100), nullable=True)
    tracking_url = Column(String(500), nullable=True)
    estimated_delivery = Column(DateTime, nullable=True)
    notes = Column(Text, nullable=True)
    coupon_code = Column(String(50), nullable=True)
    shipped_at = Column(DateTime, nullable=True)
    delivered_at = Column(DateTime, nullable=True)
    cancelled_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    user = relationship("User", back_populates="orders")
    items = relationship("OrderItem", back_populates="order", cascade="all, delete-orphan")
    payment = relationship("Payment", back_populates="order", uselist=False)
    invoice = relationship("Invoice", back_populates="order", uselist=False)
    returns = relationship("Return", back_populates="order")


class OrderItem(Base):
    __tablename__ = "order_items"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    order_id = Column(UUID(as_uuid=True), ForeignKey("orders.id", ondelete="CASCADE"), nullable=False, index=True)
    part_id = Column(UUID(as_uuid=True), ForeignKey("parts_catalog.id"), nullable=True)
    supplier_part_id = Column(UUID(as_uuid=True), ForeignKey("supplier_parts.id"), nullable=True)
    # Snapshot fields (in case part/supplier data changes later)
    part_name = Column(String(255), nullable=False)
    part_sku = Column(String(100))
    manufacturer = Column(String(100))
    part_type = Column(String(50))
    supplier_name = Column(String(255))                              # hidden from customer!
    supplier_order_id = Column(String(100), nullable=True)
    quantity = Column(Integer, default=1, nullable=False)
    unit_price = Column(Numeric(10, 2), nullable=False)             # without VAT
    vat_amount = Column(Numeric(10, 2), nullable=False)
    total_price = Column(Numeric(10, 2), nullable=False)
    warranty_months = Column(Integer, default=12)
    created_at = Column(DateTime, default=datetime.utcnow)

    # Relationships
    order = relationship("Order", back_populates="items")
    part = relationship("PartsCatalog", back_populates="order_items")
    supplier_part = relationship("SupplierPart", back_populates="order_items")


class Payment(Base):
    __tablename__ = "payments"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    order_id = Column(UUID(as_uuid=True), ForeignKey("orders.id"), nullable=False, index=True)
    payment_intent_id = Column(String(255), unique=True, nullable=True)  # Stripe
    amount = Column(Numeric(10, 2), nullable=False)
    currency = Column(String(3), default="ILS")
    status = Column(String(50), default="pending")
    # statuses: pending, succeeded, failed, refunded, partially_refunded
    payment_method = Column(String(50), nullable=True)
    stripe_customer_id = Column(String(255), nullable=True)
    last_4_digits = Column(String(4), nullable=True)
    card_brand = Column(String(50), nullable=True)
    paid_at = Column(DateTime, nullable=True)
    refunded_at = Column(DateTime, nullable=True)
    refund_amount = Column(Numeric(10, 2), nullable=True)
    refund_reason = Column(String(255), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    order = relationship("Order", back_populates="payment")


class Invoice(Base):
    __tablename__ = "invoices"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    invoice_number = Column(String(20), unique=True, nullable=False)
    order_id = Column(UUID(as_uuid=True), ForeignKey("orders.id"), nullable=False, index=True)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False, index=True)
    business_number = Column(String(50), default="060633880")        # עוסק מורשה
    pdf_path = Column(String(500), nullable=True)
    pdf_url = Column(String(500), nullable=True)
    issued_at = Column(DateTime, default=datetime.utcnow)

    # Relationships
    order = relationship("Order", back_populates="invoice")
    user = relationship("User", back_populates="invoices")


class Return(Base):
    __tablename__ = "returns"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    return_number = Column(String(20), unique=True, nullable=False)
    order_id = Column(UUID(as_uuid=True), ForeignKey("orders.id"), nullable=False, index=True)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False, index=True)
    reason = Column(String(50), nullable=False)
    # reasons: defective, wrong_item, changed_mind, damaged_shipping, other
    description = Column(Text, nullable=True)
    status = Column(String(50), default="pending")
    # statuses: pending, approved, rejected, completed, cancelled
    original_amount = Column(Numeric(10, 2), nullable=False)
    refund_amount = Column(Numeric(10, 2), nullable=True)
    refund_percentage = Column(Numeric(5, 2), nullable=True)         # 90% or 100%
    handling_fee = Column(Numeric(10, 2), nullable=True)
    tracking_number = Column(String(100), nullable=True)
    tracking_url = Column(String(500), nullable=True)
    rejection_reason = Column(String(255), nullable=True)
    requested_at = Column(DateTime, default=datetime.utcnow)
    approved_at = Column(DateTime, nullable=True)
    rejected_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)

    # Relationships
    order = relationship("Order", back_populates="returns")
    user = relationship("User", back_populates="returns")


# ==============================================================================
# 5. AI & CHAT TABLES (4)
# ==============================================================================

class Conversation(Base):
    __tablename__ = "conversations"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    title = Column(String(255), nullable=True)
    current_agent = Column(String(50), nullable=True)
    context = Column(JSONB, default=dict)
    is_active = Column(Boolean, default=True)
    started_at = Column(DateTime, default=datetime.utcnow)
    last_message_at = Column(DateTime, default=datetime.utcnow)
    ended_at = Column(DateTime, nullable=True)

    # Relationships
    user = relationship("User", back_populates="conversations")
    messages = relationship("Message", back_populates="conversation", cascade="all, delete-orphan")
    ratings = relationship("AgentRating", back_populates="conversation", cascade="all, delete-orphan")


class Message(Base):
    __tablename__ = "messages"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    conversation_id = Column(UUID(as_uuid=True), ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False, index=True)
    role = Column(String(20), nullable=False)                        # user, assistant, system
    agent_name = Column(String(50), nullable=True)
    content = Column(Text, nullable=False)
    content_type = Column(String(20), default="text")               # text, audio, video, image
    file_id = Column(UUID(as_uuid=True), ForeignKey("files.id"), nullable=True)
    transcription = Column(Text, nullable=True)
    analysis = Column(JSONB, nullable=True)
    model_used = Column(String(100), nullable=True)
    tokens_used = Column(Integer, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)

    # Relationships
    conversation = relationship("Conversation", back_populates="messages")
    actions = relationship("AgentAction", back_populates="message", cascade="all, delete-orphan")


class AgentAction(Base):
    __tablename__ = "agent_actions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    message_id = Column(UUID(as_uuid=True), ForeignKey("messages.id", ondelete="CASCADE"), nullable=False, index=True)
    agent_name = Column(String(50))
    action_type = Column(String(50))                                 # search_parts, create_order...
    action_data = Column(JSONB, default=dict)
    result = Column(JSONB, nullable=True)
    success = Column(Boolean, default=True)
    error_message = Column(Text, nullable=True)
    execution_time_ms = Column(Integer, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    # Relationships
    message = relationship("Message", back_populates="actions")


class AgentRating(Base):
    __tablename__ = "agent_ratings"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    conversation_id = Column(UUID(as_uuid=True), ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    agent_name = Column(String(50))
    rating = Column(Integer, nullable=False)
    feedback = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        CheckConstraint("rating >= 1 AND rating <= 5", name="ck_agent_rating_range"),
    )

    # Relationships
    conversation = relationship("Conversation", back_populates="ratings")
    user = relationship("User", back_populates="agent_ratings")


# ==============================================================================
# 6. FILES & MEDIA TABLES (2)
# ==============================================================================

class File(Base):
    __tablename__ = "files"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    original_filename = Column(String(255))
    stored_filename = Column(String(255), unique=True)
    file_type = Column(String(50))                                   # image, audio, video
    mime_type = Column(String(100))
    file_size_bytes = Column(BigInteger)
    compressed_size_bytes = Column(BigInteger, nullable=True)

    # Image fields
    width = Column(Integer, nullable=True)
    height = Column(Integer, nullable=True)
    image_format = Column(String(20), nullable=True)

    # Audio fields
    duration_seconds = Column(Integer, nullable=True)
    audio_codec = Column(String(50), nullable=True)

    # Video fields
    video_codec = Column(String(50), nullable=True)
    resolution = Column(String(20), nullable=True)
    fps = Column(Integer, nullable=True)

    # Storage
    storage_path = Column(String(500))
    cdn_url = Column(String(500), nullable=True)
    signed_url = Column(String(500), nullable=True)
    signed_url_expires = Column(DateTime, nullable=True)

    # Processing
    is_processed = Column(Boolean, default=False)
    virus_scan_status = Column(String(50), default="pending")
    virus_scan_at = Column(DateTime, nullable=True)

    # Lifecycle
    expires_at = Column(DateTime)
    deleted_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        Index("idx_files_expires_at", "expires_at"),
    )

    # Relationships
    user = relationship("User", back_populates="files")
    metadata_entries = relationship("FileMetadata", back_populates="file", cascade="all, delete-orphan")


class FileMetadata(Base):
    __tablename__ = "file_metadata"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    file_id = Column(UUID(as_uuid=True), ForeignKey("files.id", ondelete="CASCADE"), nullable=False, index=True)
    metadata_key = Column(String(100))
    metadata_value = Column(Text)

    # Relationships
    file = relationship("File", back_populates="metadata_entries")


# ==============================================================================
# 7. SYSTEM & LOGS TABLES (5)
# ==============================================================================

class SystemLog(Base):
    __tablename__ = "system_logs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    level = Column(String(20), nullable=False)                       # DEBUG, INFO, WARNING, ERROR, CRITICAL
    logger_name = Column(String(100))
    message = Column(Text, nullable=False)
    user_id = Column(UUID(as_uuid=True), nullable=True)
    ip_address = Column(String(45))
    endpoint = Column(String(255))
    method = Column(String(10))
    status_code = Column(Integer)
    request_data = Column(JSONB, nullable=True)
    response_data = Column(JSONB, nullable=True)
    exception = Column(Text, nullable=True)
    stack_trace = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), nullable=True, index=True)
    action = Column(String(100), nullable=False)
    entity_type = Column(String(50))
    entity_id = Column(UUID(as_uuid=True), nullable=True)
    old_value = Column(JSONB, nullable=True)
    new_value = Column(JSONB, nullable=True)
    ip_address = Column(String(45))
    user_agent = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)


class SystemSetting(Base):
    __tablename__ = "system_settings"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    key = Column(String(100), unique=True, nullable=False)
    value = Column(Text)
    value_type = Column(String(20), default="string")               # string, integer, boolean, json, float
    description = Column(Text)
    is_public = Column(Boolean, default=False)
    updated_by = Column(UUID(as_uuid=True), nullable=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class CacheEntry(Base):
    __tablename__ = "cache_entries"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    cache_key = Column(String(255), unique=True, nullable=False)
    cache_value = Column(JSONB)
    expires_at = Column(DateTime)
    created_at = Column(DateTime, default=datetime.utcnow)


class Notification(Base):
    __tablename__ = "notifications"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    type = Column(String(50))                                        # order_update, payment_success...
    title = Column(String(255))
    message = Column(Text)
    data = Column(JSONB, default=dict)
    channel = Column(String(20), default="push")                    # email, sms, whatsapp, push
    sent_at = Column(DateTime, nullable=True)
    read_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    # Relationships
    user = relationship("User", back_populates="notifications")


# ==============================================================================
# DATABASE INITIALIZATION
# ==============================================================================

async def create_tables():
    """Create all tables (used in development; production uses Alembic)."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    print("✅ All 28 tables created successfully")


async def drop_tables():
    """Drop all tables (dangerous! development only)."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


async def seed_initial_data(db: AsyncSession):
    """Seed initial system settings and suppliers."""
    from sqlalchemy import select as sa_select

    # System settings
    settings = [
        {"key": "maintenance_mode", "value": "false", "value_type": "boolean", "is_public": True, "description": "Site maintenance mode"},
        {"key": "max_upload_size_mb", "value": "25", "value_type": "integer", "is_public": True, "description": "Max file upload size"},
        {"key": "currency_exchange_rate_usd_to_ils", "value": "3.65", "value_type": "float", "is_public": True, "description": "USD to ILS rate"},
        {"key": "profit_margin_percentage", "value": "45", "value_type": "integer", "is_public": False, "description": "Profit margin %"},
        {"key": "vat_percentage", "value": "17", "value_type": "integer", "is_public": True, "description": "VAT %"},
        {"key": "default_shipping_cost_ils", "value": "91", "value_type": "integer", "is_public": True, "description": "Default shipping cost in ILS"},
        {"key": "cache_ttl_vehicles_seconds", "value": "7776000", "value_type": "integer", "is_public": False, "description": "Vehicle cache TTL (90 days)"},
        {"key": "max_login_attempts", "value": "5", "value_type": "integer", "is_public": False, "description": "Max failed logins before lockout"},
        {"key": "login_lockout_minutes", "value": "15", "value_type": "integer", "is_public": False, "description": "Lockout duration"},
        {"key": "2fa_code_expiry_minutes", "value": "10", "value_type": "integer", "is_public": False, "description": "2FA code expiry"},
        {"key": "trust_device_days", "value": "180", "value_type": "integer", "is_public": False, "description": "Trusted device duration"},
        {"key": "file_expiry_days", "value": "30", "value_type": "integer", "is_public": False, "description": "File auto-deletion"},
        {"key": "max_audio_duration_seconds", "value": "120", "value_type": "integer", "is_public": True, "description": "Max audio duration"},
        {"key": "max_video_duration_seconds", "value": "60", "value_type": "integer", "is_public": True, "description": "Max video duration"},
        {"key": "business_name", "value": "Auto Spare", "value_type": "string", "is_public": True, "description": "Business name"},
        {"key": "business_number", "value": "060633880", "value_type": "string", "is_public": True, "description": "עוסק מורשה"},
        {"key": "support_email", "value": "support@autospare.com", "value_type": "string", "is_public": True, "description": "Support email"},
    ]

    for s in settings:
        existing = await db.execute(sa_select(SystemSetting).where(SystemSetting.key == s["key"]))
        if not existing.scalar_one_or_none():
            setting = SystemSetting(
                key=s["key"],
                value=s["value"],
                value_type=s["value_type"],
                is_public=s["is_public"],
                description=s["description"],
            )
            db.add(setting)

    # Suppliers
    suppliers_data = [
        {"name": "RockAuto", "country": "USA", "website": "rockauto.com", "priority": 1},
        {"name": "FCP Euro", "country": "USA", "website": "fcpeuro.com", "priority": 2},
        {"name": "Autodoc", "country": "Germany", "website": "autodoc.de", "priority": 3},
        {"name": "AliExpress", "country": "China", "website": "aliexpress.com", "priority": 4},
    ]

    for s_data in suppliers_data:
        existing = await db.execute(sa_select(Supplier).where(Supplier.name == s_data["name"]))
        if not existing.scalar_one_or_none():
            supplier = Supplier(**s_data, is_active=True)
            db.add(supplier)

    await db.commit()
    print("✅ Seed data inserted (system settings + suppliers)")


if __name__ == "__main__":
    import asyncio

    async def main():
        await create_tables()
        async for db in get_db():
            await seed_initial_data(db)
            break

    asyncio.run(main())
