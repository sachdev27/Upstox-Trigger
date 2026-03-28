import logging
from fastapi import APIRouter, HTTPException, Query
from app.notifications.manager import get_notification_manager

router = APIRouter(prefix="/notifications", tags=["Notifications"])
logger = logging.getLogger(__name__)

@router.post("/test")
async def test_notification(channel: str = Query("email")):
    """Send a test notification to the specified channel."""
    manager = get_notification_manager()
    
    subject = "⚡ Upstox Terminal: Test Alert"
    body = "This is a test notification from your Upstox Trading Automation terminal. If you received this, your configuration is correct! ✅"
    
    # We use send_alert which handles channel filtering internally
    # But for a specific test, we might want to force a channel.
    # Our current manager.send_alert uses Settings.NOTIFICATION_CHANNELS.
    
    success = await manager.send_alert(subject, body)
    
    if success:
        return {"status": "success", "message": f"Test alert dispatched to {channel}"}
    else:
        return {"status": "error", "message": "Failed to dispatch test alert. Check logs or settings."}
