from abc import ABC, abstractmethod

class Agent(ABC):
    def __init__(self, name):
        self.name = name
        self.status = "idle"
        
    @abstractmethod
    def execute_task(self, *args, **kwargs):
        pass
