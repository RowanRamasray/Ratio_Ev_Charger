"""Select entity for Ratio EV Charger integration."""

from __future__ import annotations

import logging
import uuid
from typing import Any

from homeassistant.components.select import SelectEntity
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .coordinator import ChargerCoordinator
from .api import RatioAPI
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

CHARGING_MODES = ["PureSolar", "SmartSolar", "Smart"]


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up select entities."""
    data = hass.data[DOMAIN][entry.entry_id]
    coordinator: ChargerCoordinator = data["coordinator"]
    api: RatioAPI = data["api"]
    user_id: str = data["user_id"]
    charger_id: str = data["charger_id"]

    entities = [
        RatioChargingModeSelect(coordinator, api, user_id, charger_id),
    ]

    async_add_entities(entities)


class RatioChargingModeSelect(CoordinatorEntity, SelectEntity):
    """Select entity for charging mode."""

    def __init__(
        self,
        coordinator: ChargerCoordinator,
        api: RatioAPI,
        user_id: str,
        charger_id: str,
    ) -> None:
        """Initialize the select entity."""
        super().__init__(coordinator)
        self._api = api
        self._user_id = user_id
        self._charger_id = charger_id
        self._attr_unique_id = f"{charger_id}_charging_mode"
        self._attr_name = "Charging Mode"
        self._attr_icon = "mdi:sun-clock"
        self._attr_options = CHARGING_MODES
        self._attr_current_option = None

    @property
    def device_info(self) -> DeviceInfo:
        """Return device information."""
        return DeviceInfo(
            identifiers={(DOMAIN, self._charger_id)},
            name="Ratio EV Charger",
            manufacturer="Ratio",
            model="EV Charger",
        )

    @property
    def options(self) -> list[str]:
        """Return the available charging mode options."""
        if self.coordinator.data is None:
            return self._attr_options

        try:
            settings = self.coordinator.data.get("settings", {})
            user_settings = settings.get("userSettings", {})
            charging_mode = user_settings.get("chargingMode", {}) or {}
            allowed = charging_mode.get("allowedValues")
            if isinstance(allowed, list) and all(isinstance(v, str) for v in allowed):
                return allowed
        except (AttributeError, KeyError, TypeError):
            pass

        return self._attr_options

    @property
    def current_option(self) -> str | None:
        """Return the current charging mode."""
        if self.coordinator.data is None:
            return self._attr_current_option

        try:
            settings = self.coordinator.data.get("settings", {})
            user_settings = settings.get("userSettings", {})
            charging_mode = user_settings.get("chargingMode", {}) or {}
            value = charging_mode.get("value")
            if isinstance(value, str):
                return value
        except (AttributeError, KeyError, TypeError):
            pass

        return self._attr_current_option

    @property
    def available(self) -> bool:
        """Return if entity is available."""
        return self.coordinator.last_update_success

    async def async_select_option(self, option: str) -> None:
        """Change the charging mode."""
        options = self.options
        if option not in options:
            _LOGGER.error("Invalid charging mode: %s", option)
            return

        previous_option = self.current_option

        # Optimistically update state so the UI updates immediately.
        self._attr_current_option = option
        self.async_write_ha_state()

        try:
            body = {
                "transactionId": str(uuid.uuid4())[:16],
                "userSettings": {
                    "chargingMode": option
                }
            }

            await self._api.async_set_charging_mode(
                self._user_id,
                self._charger_id,
                body,
            )

            _LOGGER.info("Charging mode changed to: %s", option)

            # Refresh coordinator data to reflect authoritative backend state
            await self.coordinator.async_refresh()

        except Exception as err:
            _LOGGER.error("Failed to set charging mode: %s", err)

            # Revert optimistic update if the API call fails
            self._attr_current_option = previous_option
            self.async_write_ha_state()
            raise
