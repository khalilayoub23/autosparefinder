from flask import Blueprint, jsonify, request
from .database import get_db
from models.base import PartCategory, OrderStatus
from api.crud import InventoryManager, OrderManager, PartManager

api = Blueprint('api', __name__)

# Parts Management
@api.route('/parts/search', methods=['GET'])
def search_parts():
    query = request.args.get('q', '')
    category = request.args.get('category')
    in_stock = request.args.get('in_stock', 'false').lower() == 'true'
    
    if category:
        category = PartCategory(category)
    
    db = next(get_db())
    parts = PartManager.search_parts(db, query, category, in_stock)
    
    return jsonify([{
        'id': p.id,
        'name': p.name,
        'part_number': p.part_number,
        'category': p.category.value,
        'retail_price': p.retail_price
    } for p in parts])

# Inventory Management
@api.route('/inventory/low-stock', methods=['GET'])
def check_low_stock():
    db = next(get_db())
    low_stock_items = InventoryManager.check_low_stock(db)
    
    return jsonify([{
        'part_number': item.part.part_number,
        'quantity': item.quantity,
        'min_stock': item.min_stock,
        'agent': item.agent.name
    } for item in low_stock_items])

# Order Processing
@api.route('/orders', methods=['POST'])
def create_order():
    data = request.json
    db = next(get_db())
    
    try:
        order = OrderManager.create_order(
            db,
            agent_id=data['agent_id'],
            customer_data=data['customer'],
            items=data['items']
        )
        
        return jsonify({
            'order_number': order.order_number,
            'total_amount': order.total_amount,
            'status': order.status.value
        }), 201
        
    except ValueError as e:
        return jsonify({'error': str(e)}), 400

# Register blueprint with main app
def init_routes(app):
    app.register_blueprint(api, url_prefix='/api/v1')

from flask import Blueprint, jsonify, request
from .database import get_db
from models.base import PartCategory, OrderStatus
from api.crud import InventoryManager, OrderManager, PartManager

api = Blueprint('api', __name__)

@api.route('/test', methods=['GET'])
def test_route():
    return jsonify({"message": "API is working"})

from flask import Blueprint, jsonify, request
from .database import get_db
from models.base import PartCategory, OrderStatus
from api.crud import InventoryManager, OrderManager, PartManager

api = Blueprint('api', __name__)

@api.route('/parts', methods=['GET'])
def get_parts():
    from models.base import Part
    db = next(get_db())
    parts = db.query(Part).all()
    return jsonify([{
        'id': p.id,
        'name': p.name,
        'part_number': p.part_number,
        'category': p.category.value if p.category else None
    } for p in parts])

@api.route('/debug/db', methods=['GET'])
def debug_db():
    from models.base import Part
    from models.base import Agent
    db = next(get_db())
    return jsonify({
        "database_connected": True,
        "parts_count": db.query(Part).count(),
        "agents_count": db.query(Agent).count()
    })
