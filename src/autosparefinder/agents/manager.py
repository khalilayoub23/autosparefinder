class AgentManager:
    def __init__(self):
        self.agents = {
            'price_finder': PriceAgent("Price Finder Alpha"),
            'inventory_tracker': InventoryAgent("Inventory Master"),
            'shipping_coordinator': ShippingAgent("Shipping Pro"),
            'quality_inspector': QualityAgent("Quality Guardian"),
            'supplier_liaison': SupplierAgent("Supplier Connect"),
            'customer_support': CustomerServiceAgent("Customer Care Bot"),
            'market_analyst': MarketAnalysisAgent("Market Sage"),
            'compatibility_checker': CompatibilityAgent("Compatibility Expert"),
            'auth_validator': AuthenticationAgent("Security Sentinel"),
            'system_optimizer': OptimizationAgent("Performance Optimizer")
        }
    
    def get_agent(self, agent_name):
        return self.agents.get(agent_name)
    
    def execute_agent_task(self, agent_name, *args, **kwargs):
        agent = self.get_agent(agent_name)
        if agent:
            return agent.execute_task(*args, **kwargs)
        return None
