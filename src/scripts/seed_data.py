from models.base import Part, Agent, Inventory, PartCategory
from config.database import SessionLocal

def seed_test_data():
    db = SessionLocal()
    
    # Create test agent
    agent = Agent(
        name="Test Agent",
        email="test@agent.com",
        phone="1234567890"
    )
    db.add(agent)
    
    # Create test parts
    engine_part = Part(
        name="V8 Engine",
        part_number="ENG-001",
        category=PartCategory.ENGINE,
        manufacturer="AutoCorp",
        base_price=1000.00
    )
    db.add(engine_part)
    
    # Create inventory
    inventory = Inventory(
        agent=agent,
        part=engine_part,
        quantity=10
    )
    db.add(inventory)
    
    db.commit()

if __name__ == "__main__":
    seed_test_data()
