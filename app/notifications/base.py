from abc import ABC, abstractmethod
from app.config import Settings

class BaseNotificationProvider(ABC):
    """Abstract base class for notification channels."""

    def __init__(self, settings: Settings):
        self.settings = settings

    @abstractmethod
    async def send(self, subject: str, body: str, html: str | None = None, **kwargs) -> bool:
        """Send a notification. Returns True if successful."""
        pass
