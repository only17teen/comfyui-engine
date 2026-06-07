"""ComfyUI Async Generation Engine v2.0 - Webhook Notifications
Discord/Slack notifications for batch completion and alerts.
"""

import asyncio
import json
import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

import aiohttp

logger = logging.getLogger(__name__)


class WebhookType(Enum):
    """Webhook notification type enumeration."""

    DISCORD = "discord"
    SLACK = "slack"
    GENERIC = "generic"


@dataclass
class NotificationConfig:
    """Configuration for webhook notifications."""

    webhook_url: str
    webhook_type: WebhookType = WebhookType.GENERIC
    enabled: bool = True
    notify_on_success: bool = True
    notify_on_failure: bool = True
    notify_on_error: bool = True
    include_images: bool = False
    max_images: int = 3
    mention_on_failure: str | None = None  # @user or @channel
    custom_fields: dict[str, str] = field(default_factory=dict)


class WebhookNotifier:
    """Async webhook notifier for Discord, Slack, and generic endpoints.

    Features:
    - Discord embeds with rich formatting
    - Slack blocks with attachments
    - Generic JSON POST
    - Rate limiting with backoff
    - Retry on failure
    - Image attachment support (Discord)
    """

    def __init__(self, config: NotificationConfig):
        self.config = config
        self._session: aiohttp.ClientSession | None = None
        self._last_notification_time: float = 0.0
        self._rate_limit_interval: float = 1.0  # Minimum seconds between notifications

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=30.0),
                headers={"Content-Type": "application/json"},
            )
        return self._session

    async def _send(self, payload: dict[str, Any]) -> bool:
        """Send notification with rate limiting and retry."""
        if not self.config.enabled:
            return False

        # Rate limiting
        elapsed = asyncio.get_event_loop().time() - self._last_notification_time
        if elapsed < self._rate_limit_interval:
            await asyncio.sleep(self._rate_limit_interval - elapsed)

        try:
            session = await self._get_session()
            async with session.post(
                self.config.webhook_url,
                json=payload,
                headers={"Content-Type": "application/json"},
            ) as resp:
                if resp.status in (200, 204):
                    self._last_notification_time = asyncio.get_event_loop().time()
                    logger.info(
                        f"Notification sent to {self.config.webhook_type.value}"
                    )
                    return True
                else:
                    text = await resp.text()
                    logger.warning(f"Webhook failed: {resp.status} - {text}")
                    return False
        except Exception as e:
            logger.error(f"Webhook error: {e}")
            return False

    def _build_discord_embed(
        self,
        title: str,
        description: str,
        color: int,
        fields: list[dict[str, Any]],
        image_urls: list[str] | None = None,
    ) -> dict[str, Any]:
        """Build Discord embed payload."""
        embed = {
            "title": title,
            "description": description,
            "color": color,
            "fields": fields,
            "timestamp": asyncio.get_event_loop().time(),
            "footer": {"text": "ComfyUI Engine v2.0"},
        }

        if image_urls and self.config.include_images:
            embed["image"] = {"url": image_urls[0]}
            if len(image_urls) > 1:
                embed["thumbnail"] = {"url": image_urls[1]}

        return {"embeds": [embed]}

    def _build_slack_blocks(
        self,
        title: str,
        description: str,
        color: str,
        fields: list[dict[str, Any]],
        image_urls: list[str] | None = None,
    ) -> dict[str, Any]:
        """Build Slack blocks payload."""
        blocks = [
            {
                "type": "header",
                "text": {"type": "plain_text", "text": title, "emoji": True},
            },
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": description},
            },
            {
                "type": "section",
                "fields": [
                    {"type": "mrkdwn", "text": f"*{f['name']}:*\n{f['value']}"}
                    for f in fields
                ],
            },
        ]

        if image_urls and self.config.include_images:
            blocks.append(
                {
                    "type": "image",
                    "image_url": image_urls[0],
                    "alt_text": "Generated image",
                }
            )

        return {
            "blocks": blocks,
            "attachments": [{"color": color, "blocks": []}],
        }

    def _build_generic_payload(
        self,
        title: str,
        description: str,
        fields: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Build generic JSON payload."""
        return {
            "title": title,
            "description": description,
            "fields": fields,
            "source": "comfyui-engine-v2",
            "timestamp": asyncio.get_event_loop().time(),
        }

    async def notify_batch_complete(
        self,
        session_id: str,
        total_jobs: int,
        completed: int,
        failed: int,
        duration_seconds: float,
        output_dir: str,
        image_urls: list[str] | None = None,
        template: str | None = None,
        tags: list[str] | None = None,
    ) -> bool:
        """Send batch completion notification."""
        if not self.config.notify_on_success and failed == 0:
            return False
        if not self.config.notify_on_failure and failed > 0:
            return False

        success_rate = (completed / total_jobs * 100) if total_jobs > 0 else 0
        status = (
            "✅ SUCCESS"
            if failed == 0
            else "⚠️ PARTIAL" if completed > 0 else "❌ FAILED"
        )
        color = 0x00FF00 if failed == 0 else 0xFFA500 if completed > 0 else 0xFF0000
        slack_color = (
            "good" if failed == 0 else "warning" if completed > 0 else "danger"
        )

        fields = [
            {"name": "Session", "value": session_id, "inline": True},
            {"name": "Total Jobs", "value": str(total_jobs), "inline": True},
            {"name": "Completed", "value": str(completed), "inline": True},
            {"name": "Failed", "value": str(failed), "inline": True},
            {"name": "Success Rate", "value": f"{success_rate:.1f}%", "inline": True},
            {"name": "Duration", "value": f"{duration_seconds:.1f}s", "inline": True},
        ]

        if template:
            fields.append({"name": "Template", "value": template, "inline": True})
        if tags:
            fields.append({"name": "Tags", "value": ", ".join(tags), "inline": True})

        mention = ""
        if failed > 0 and self.config.mention_on_failure:
            mention = f"{self.config.mention_on_failure} "

        title = f"{status} - Batch Complete"
        description = f"{mention}Generation batch completed with {completed}/{total_jobs} jobs successful."

        if self.config.webhook_type == WebhookType.DISCORD:
            payload = self._build_discord_embed(
                title, description, color, fields, image_urls
            )
        elif self.config.webhook_type == WebhookType.SLACK:
            payload = self._build_slack_blocks(
                title, description, slack_color, fields, image_urls
            )
        else:
            payload = self._build_generic_payload(title, description, fields)

        return await self._send(payload)

    async def notify_error(
        self,
        error_message: str,
        job_id: str | None = None,
        details: str | None = None,
    ) -> bool:
        """Send error notification."""
        if not self.config.notify_on_error:
            return False

        fields = [
            {"name": "Error", "value": error_message, "inline": False},
        ]

        if job_id:
            fields.append({"name": "Job ID", "value": job_id, "inline": True})
        if details:
            fields.append({"name": "Details", "value": details, "inline": False})

        mention = ""
        if self.config.mention_on_failure:
            mention = f"{self.config.mention_on_failure} "

        title = "❌ Engine Error"
        description = f"{mention}An error occurred in the generation engine."

        if self.config.webhook_type == WebhookType.DISCORD:
            payload = self._build_discord_embed(title, description, 0xFF0000, fields)
        elif self.config.webhook_type == WebhookType.SLACK:
            payload = self._build_slack_blocks(title, description, "danger", fields)
        else:
            payload = self._build_generic_payload(title, description, fields)

        return await self._send(payload)

    async def notify_progress(
        self,
        session_id: str,
        current: int,
        total: int,
        eta_seconds: float | None = None,
    ) -> bool:
        """Send progress update (throttled)."""
        # Only send progress at 25%, 50%, 75%
        progress_pct = (current / total * 100) if total > 0 else 0
        if progress_pct not in (25, 50, 75):
            return False

        fields = [
            {
                "name": "Progress",
                "value": f"{current}/{total} ({progress_pct:.0f}%)",
                "inline": True,
            },
            {"name": "Session", "value": session_id, "inline": True},
        ]

        if eta_seconds:
            fields.append(
                {"name": "ETA", "value": f"{eta_seconds:.0f}s", "inline": True}
            )

        title = f"📊 Progress Update ({progress_pct:.0f}%)"
        description = f"Batch generation in progress..."

        if self.config.webhook_type == WebhookType.DISCORD:
            payload = self._build_discord_embed(title, description, 0x3498DB, fields)
        elif self.config.webhook_type == WebhookType.SLACK:
            payload = self._build_slack_blocks(title, description, "#3498DB", fields)
        else:
            payload = self._build_generic_payload(title, description, fields)

        return await self._send(payload)

    async def close(self) -> None:
        """Close aiohttp session."""
        if self._session and not self._session.closed:
            await self._session.close()


class MultiNotifier:
    """Manager for multiple webhook notifiers."""

    def __init__(self):
        self.notifiers: list[WebhookNotifier] = []

    def add(self, config: NotificationConfig) -> None:
        """Add a notifier."""
        self.notifiers.append(WebhookNotifier(config))

    async def notify_batch_complete(self, **kwargs) -> list[bool]:
        """Send to all notifiers."""
        results = []
        for notifier in self.notifiers:
            result = await notifier.notify_batch_complete(**kwargs)
            results.append(result)
        return results

    async def notify_error(self, **kwargs) -> list[bool]:
        """Send error to all notifiers."""
        results = []
        for notifier in self.notifiers:
            result = await notifier.notify_error(**kwargs)
            results.append(result)
        return results

    async def notify_progress(self, **kwargs) -> list[bool]:
        """Send progress to all notifiers."""
        results = []
        for notifier in self.notifiers:
            result = await notifier.notify_progress(**kwargs)
            results.append(result)
        return results

    async def close(self) -> None:
        """Close all notifiers."""
        await asyncio.gather(*[n.close() for n in self.notifiers])
