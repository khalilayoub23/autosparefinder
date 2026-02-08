"""initial schema

Revision ID: 0001_initial
Revises: 
Create Date: 2026-02-08 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = '0001_initial'
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    # Create enums
    partcategory = sa.Enum('engine', 'transmission', 'suspension', 'electrical', 'body', 'interior', name='partcategory')
    orderstatus = sa.Enum('pending', 'confirmed', 'shipped', 'delivered', 'cancelled', name='orderstatus')
    partcategory.create(op.get_bind(), checkfirst=True)
    orderstatus.create(op.get_bind(), checkfirst=True)

    # agents
    op.create_table(
        'agents',
        sa.Column('id', sa.Integer, primary_key=True),
        sa.Column('name', sa.String(length=100), nullable=False),
        sa.Column('email', sa.String(length=100), nullable=False, unique=True),
        sa.Column('phone', sa.String(length=20)),
        sa.Column('address', sa.Text),
        sa.Column('is_active', sa.Boolean(), server_default=sa.text('true')),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
    )

    # parts
    op.create_table(
        'parts',
        sa.Column('id', sa.Integer, primary_key=True),
        sa.Column('name', sa.String(length=200), nullable=False),
        sa.Column('part_number', sa.String(length=50), nullable=False, unique=True),
        sa.Column('category', partcategory, nullable=True),
        sa.Column('manufacturer', sa.String(length=100)),
        sa.Column('description', sa.Text),
        sa.Column('specifications', sa.Text),
        sa.Column('weight', sa.Float),
        sa.Column('dimensions', sa.String(length=50)),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('base_price', sa.Float(), nullable=False),
        sa.Column('markup_percentage', sa.Float(), nullable=True),
    )

    # inventory
    op.create_table(
        'inventory',
        sa.Column('id', sa.Integer, primary_key=True),
        sa.Column('agent_id', sa.Integer, sa.ForeignKey('agents.id')),
        sa.Column('part_id', sa.Integer, sa.ForeignKey('parts.id')),
        sa.Column('quantity', sa.Integer(), server_default='0'),
        sa.Column('min_stock', sa.Integer(), server_default='5'),
        sa.Column('max_stock', sa.Integer(), server_default='100'),
        sa.Column('location', sa.String(length=50)),
        sa.Column('last_restock_date', sa.DateTime(), nullable=True),
        sa.UniqueConstraint('agent_id', 'part_id', name='unique_agent_part'),
    )

    # orders
    op.create_table(
        'orders',
        sa.Column('id', sa.Integer, primary_key=True),
        sa.Column('order_number', sa.String(length=20), unique=True),
        sa.Column('agent_id', sa.Integer, sa.ForeignKey('agents.id')),
        sa.Column('customer_name', sa.String(length=100)),
        sa.Column('customer_email', sa.String(length=100)),
        sa.Column('customer_phone', sa.String(length=20)),
        sa.Column('status', orderstatus, server_default='pending'),
        sa.Column('total_amount', sa.Float()),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
    )

    # order_items
    op.create_table(
        'order_items',
        sa.Column('id', sa.Integer, primary_key=True),
        sa.Column('order_id', sa.Integer, sa.ForeignKey('orders.id')),
        sa.Column('part_id', sa.Integer, sa.ForeignKey('parts.id')),
        sa.Column('quantity', sa.Integer()),
        sa.Column('price', sa.Float()),
    )

    # association table: agent_specializations
    op.create_table(
        'agent_specializations',
        sa.Column('agent_id', sa.Integer, sa.ForeignKey('agents.id')),
        sa.Column('category', partcategory),
    )


def downgrade():
    op.drop_table('agent_specializations')
    op.drop_table('order_items')
    op.drop_table('orders')
    op.drop_table('inventory')
    op.drop_table('parts')
    op.drop_table('agents')

    # drop enums
    orderstatus = sa.Enum(name='orderstatus')
    partcategory = sa.Enum(name='partcategory')
    orderstatus.drop(op.get_bind(), checkfirst=True)
    partcategory.drop(op.get_bind(), checkfirst=True)
