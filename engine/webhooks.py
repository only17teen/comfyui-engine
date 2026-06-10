"""Notification channels for job completion/failure.

Addresses Issue #37: Notification channels — Slack, Discord, email on job complete/fail.
"""
import logging
import httpx
import asyncio

logger = logging.getLogger(__name__)

class NotificationManager:
    async def notify_slack(self, webhook_url: str, message: str):
        if not webhook_url: return
        try:
            async with httpx.AsyncClient() as client:
                await client.post(webhook_url, json={"text": message})
        except Exception as e:
            logger.error(f"Slack notification failed: {e}")

    async def notify_discord(self, webhook_url: str, message: str):
        if not webhook_url: return
        try:
            async with httpx.AsyncClient() as client:
                await client.post(webhook_url, json={"content": message})
        except Exception as e:
            logger.error(f"Discord notification failed: {e}")

    async def notify_job_event(self, job_id: str, status: str, channels: dict):
        """Channels dict format: {'slack': 'url', 'discord': 'url'}"""
        message = f"Job {job_id} changed status to: {status}"
        tasks = []
        if 'slack' in channels:
            tasks.append(self.notify_slack(channels['slack'], message))
        if 'discord' in channels:
            tasks.append(self.notify_discord(channels['discord'], message))
            
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

notifier = NotificationManager()
