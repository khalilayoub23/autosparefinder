class InventoryManager:
    def __init__(self):
        self.parts = {}
        self.suppliers = {}
        self.alerts = []
        
    def check_stock_levels(self):
        for part_id, part in self.parts.items():
            if part.quantity <= part.reorder_level:
                self.create_alert(part_id)
                
    def create_alert(self, part_id):
        alert = {
            'part_id': part_id,
            'timestamp': datetime.now(),
            'type': 'REORDER'
        }
        self.alerts.append(alert)
