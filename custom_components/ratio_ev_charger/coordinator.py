"""DataUpdateCoordinator for fetching charger state."""

from __future__ import annotations

import asyncio
import logging
from datetime import timedelta
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import RatioAPI

_LOGGER = logging.getLogger(__name__)


class ChargerCoordinator(DataUpdateCoordinator):
    """Coordinator for polling charger state."""

    def __init__(
        self,
        hass: HomeAssistant,
        api: RatioAPI,
        user_id: str,
        charger_id: str,
        update_interval: int = 60,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name="ratio_ev_charger",
            update_interval=timedelta(seconds=update_interval),
        )
        self.api = api
        self.user_id = user_id
        self.charger_id = charger_id

    async def _async_update_data(self) -> Any:
        try:
            _LOGGER.debug(
                "Fetching charger status and user settings for user_id=%s, charger_id=%s",
                self.user_id,
                self.charger_id,
            )

            status_task = self.api.async_get_status(self.user_id, self.charger_id)
            settings_task = self.api.async_get_user_settings(self.user_id, self.charger_id)
            status, settings = await asyncio.gather(status_task, settings_task)

            merged = {
                "status": status,
                "settings": settings,
            }

            _LOGGER.debug("Successfully fetched charger status and user settings")
            return merged
        except Exception as err:
            _LOGGER.error("Failed to fetch charger status or settings: %s", err)
            raise UpdateFailed(f"Error fetching charger status or settings: {err}") from err
