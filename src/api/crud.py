from sqlalchemy.orm import Session
from sqlalchemy import and_, or_
from models.base import *
from typing import List, Optional
from datetime import datetime

class InventoryManager:
    @staticmethod
    def check_low_stock(db: Session) -> List[Inventory]:
        return db.query(Inventory).filter(
            Inventory.quantity <= Inventory.min_stock
        ).all()
    
    @staticmethod
    def restock_needed(db: Session, agent_id: int) -> List[Inventory]:
        return db.query(Inventory).filter(
            and_(
                Inventory.agent_id == agent_id,
                Inventory.quantity <= Inventory.min_stock
            )
        ).all()

class OrderManager:
    @staticmethod
    def create_order(db: Session, agent_id: int, customer_data: dict, items: List[dict]):
        # Generate unique order number
        order_number = f"ORD-{datetime.utcnow().strftime('%Y%m%d%H%M%S')}"
        
        order = Order(
            order_number=order_number,
            agent_id=agent_id,
            customer_name=customer_data['name'],
            customer_email=customer_data.get('email'),
            customer_phone=customer_data.get('phone')
        )
        
        total_amount = 0
        for item in items:
            part = db.query(Part).get(item['part_id'])
            if not part:
                raise ValueError(f"Part {item['part_id']} not found")
                
            inventory = db.query(Inventory).filter(
                and_(
                    Inventory.agent_id == agent_id,
                    Inventory.part_id == part.id
                )
            ).first()
            
            if not inventory or inventory.quantity < item['quantity']:
                raise ValueError(f"Insufficient stock for part {part.part_number}")
            
            order_item = OrderItem(
                part_id=part.id,
                quantity=item['quantity'],
                price=part.retail_price
            )
            order.items.append(order_item)
            total_amount += order_item.price * order_item.quantity
            
            # Update inventory
            inventory.quantity -= item['quantity']
        
        order.total_amount = total_amount
        db.add(order)
        db.commit()
        return order

class PartManager:
    @staticmethod
    def search_parts(
        db: Session,
        query: str,
        category: Optional[PartCategory] = None,
        in_stock: bool = False
    ) -> List[Part]:
        filters = []
        if query:
            filters.append(
                or_(
                    Part.name.ilike(f"%{query}%"),
                    Part.part_number.ilike(f"%{query}%"),
                    Part.manufacturer.ilike(f"%{query}%")
                )
            )
        if category:
            filters.append(Part.category == category)
        
        parts = db.query(Part)
        if filters:
            parts = parts.filter(and_(*filters))
            
        if in_stock:
            parts = parts.join(Inventory).filter(Inventory.quantity > 0)
            
        return parts.all()

def create_agent(db: Session, agent_data: dict):
    db_agent = Agent(**agent_data)
    db.add(db_agent)
    db.commit()
    db.refresh(db_agent)
    return db_agent

def get_agent(db: Session, agent_name: str):
    return db.query(Agent).filter(Agent.name == agent_name).first()

def get_all_agents(db: Session, skip: int = 0, limit: int = 100):
    return db.query(Agent).offset(skip).limit(limit).all()

def update_agent(db: Session, agent_name: str, agent_data: dict):
    db_agent = get_agent(db, agent_name)
    for key, value in agent_data.items():
        setattr(db_agent, key, value)
    db.commit()
    return db_agent

def create_part(db: Session, part_data: dict):
    db_part = Part(**part_data)
    db.add(db_part)
    db.commit()
    db.refresh(db_part)
    return db_part

def update_inventory(db: Session, agent_id: int, part_id: int, quantity: int, price: float):
    inventory = db.query(Inventory).filter(
        Inventory.agent_id == agent_id,
        Inventory.part_id == part_id
    ).first()
    
    if inventory:
        inventory.quantity = quantity
        inventory.price = price
    else:
        inventory = Inventory(
            agent_id=agent_id,
            part_id=part_id,
            quantity=quantity,
            price=price
        )
        db.add(inventory)
    
    db.commit()
    return inventory