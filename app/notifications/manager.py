import logging
import asyncio
from typing import Dict, Type
from app.config import get_settings
from .base import BaseNotificationProvider
from .email import EmailProvider

logger = logging.getLogger(__name__)

class NotificationManager:
    """Unified entry point for sending alerts across registered providers."""

    def __init__(self):
        self.settings = get_settings()
        self._providers: Dict[str, BaseNotificationProvider] = {}
        
        # Register default providers
        self.register_provider("EMAIL", EmailProvider(self.settings))
        # WhatsApp excluded as requested by user

    def register_provider(self, name: str, provider: BaseNotificationProvider):
        """Add a new notification channel provider."""
        self._providers[name.upper()] = provider
        logger.debug(f"Registered notification provider: {name}")

    async def send_alert(self, subject: str, body: str, html: str | None = None, channels_override: list[str] | None = None) -> bool:
        """Broadcast an alert to enabled or overridden notification channels."""
        if channels_override:
            channels = [c.upper() for c in channels_override]
        else:
            channels = [c.strip().upper() for r in self.settings.NOTIFICATION_CHANNELS.split(",") for c in r.split(",")]
        
        logger.info(f"Dispatching alert via channels: {channels}")
        
        tasks = []
        task_names = []

        for channel in channels:
            provider = self._providers.get(channel)
            if provider:
                tasks.append(provider.send(subject, body, html=html))
                task_names.append(channel)
            else:
                logger.warning(f"Notification channel '{channel}' not found or not registered.")

        if not tasks:
            logger.debug("No notification channels enabled or selected.")
            return False

        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        any_success = False
        for i, res in enumerate(results):
            if isinstance(res, Exception):
                logger.error(f"Error sending {task_names[i]} notification: {res}")
            elif res is True:
                any_success = True
            else:
                logger.warning(f"{task_names[i]} notification failed (False returned).")
                
        return any_success

# Singleton manager
_manager = None

def get_notification_manager() -> NotificationManager:
    global _manager
    if _manager is None:
        _manager = NotificationManager()
    return _manager
