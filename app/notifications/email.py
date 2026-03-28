import logging
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from app.config import Settings
from .base import BaseNotificationProvider

logger = logging.getLogger(__name__)

class EmailProvider(BaseNotificationProvider):
    """SMTP wrapper for email notifications."""

    async def send(self, subject: str, body: str, html: str | None = None, **kwargs) -> bool:
        """Send an email alert via SMTP."""
        if not self.settings.SMTP_USER or not self.settings.SMTP_PASSWORD:
            logger.warning("SMTP not configured.")
            return False

        server = self.settings.SMTP_SERVER
        port = self.settings.SMTP_PORT
        sender = self.settings.SMTP_USER
        
        if not server or not sender:
            logger.warning("SMTP server or user not configured.")
            return False

        recipients = [r.strip() for r in self.settings.EMAIL_RECIPIENT.split(",") if r.strip()]
        if not recipients:
            logger.warning("Email recipient list missing.")
            return False

        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = sender
        msg["To"] = ", ".join(recipients)

        msg.attach(MIMEText(body, "plain"))
        if html:
            msg.attach(MIMEText(html, "html"))

        try:
            # Determine if we should use SMTP_SSL or STARTTLS
            if port == 465:
                smtp_class = smtplib.SMTP_SSL
            else:
                smtp_class = smtplib.SMTP

            # Note: SMTP is inherently blocking, but for simple alerts this is usually acceptable.
            # In a more advanced setup, we would use an async SMTP client like aiosmtplib.
            with smtp_class(server, port, timeout=15) as smtp:
                if port != 465:
                    smtp.starttls()
                
                smtp.login(sender, self.settings.SMTP_PASSWORD)
                smtp.sendmail(sender, recipients, msg.as_string())
                logger.info(f"Email alert sent successfully to {len(recipients)} recipients")
                return True
        except Exception as e:
            logger.error(f"Failed to send email via SMTP: {e}")
            return False
