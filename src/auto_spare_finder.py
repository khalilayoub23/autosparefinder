class AutoSpareFinder:
    def __init__(self):
        self.parts_database = {}
        
    def add_part(self, part_id, name, location, quantity):
        self.parts_database[part_id] = {
            'name': name,
            'location': location,
            'quantity': quantity
        }
    
    def find_part(self, part_id):
        if part_id in self.parts_database:
            return self.parts_database[part_id]
        return None
    
    def update_quantity(self, part_id, new_quantity):
        if part_id in self.parts_database:
            self.parts_database[part_id]['quantity'] = new_quantity
            return True
        return False
