"""Switch entity for Ratio EV Charger integration."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.switch import SwitchEntity, SwitchDeviceClass
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .coordinator import ChargerCoordinator
from .api import RatioAPI
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up switch entities."""
    data = hass.data[DOMAIN][entry.entry_id]
    coordinator: ChargerCoordinator = data["coordinator"]
    api: RatioAPI = data["api"]
    user_id: str = data["user_id"]
    charger_id: str = data["charger_id"]
    vehicle_id: str | None = data.get("vehicle_id")

    entities = [
        RatioChargingSwitch(coordinator, api, user_id, charger_id, vehicle_id),
    ]

    async_add_entities(entities)


class RatioChargingSwitch(CoordinatorEntity, SwitchEntity):
    """Switch for controlling charging."""

    def __init__(
        self,
        coordinator: ChargerCoordinator,
        api: RatioAPI,
        user_id: str,
        charger_id: str,
        vehicle_id: str | None,
    ) -> None:
        """Initialize the switch."""
        super().__init__(coordinator)
        self._api = api
        self._user_id = user_id
        self._charger_id = charger_id
        self._vehicle_id = vehicle_id
        self._attr_unique_id = f"{charger_id}_charging"
        self._attr_name = "Charge Control"
        self._attr_device_class = SwitchDeviceClass.SWITCH
        self._attr_icon = "mdi:lightning-bolt-outline"

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
    def is_on(self) -> bool | None:
        """Return if charging is active.

        Switch is ON when:
        - charging: active charging in progress
        - paused: charging session paused

        Switch is OFF when:
        - error: charger in error state
        - unauthorized: charging not authorized
        - waiting_for_vehicle: vehicle not connected
        - idle: connected but not charging
        """
        if self.coordinator.data is None:
            return None

        try:
            data = self.coordinator.data
            status = data.get("status", data)
            indicators = status.get("chargerStatus", {}).get("indicators", {})

            charging_state = indicators.get("chargingState")
            is_paused = indicators.get("isChargingPaused")
            is_connected = indicators.get("isVehicleConnected")
            is_authorized = indicators.get("isChargingAuthorized")
            errors = indicators.get("errors") or []

            # Error state → OFF
            if isinstance(charging_state, str) and charging_state.strip().lower() == "error":
                return False
            if isinstance(errors, (list, tuple)) and len(errors) > 0:
                return False

            # Unauthorized → OFF
            if is_authorized is False:
                return False

            # Paused → ON
            if is_paused is True:
                return True

            # Not connected → OFF
            if is_connected is False:
                return False

            # Check if actively charging
            if isinstance(charging_state, str):
                cs = charging_state.strip().lower()
                if cs == "charging":
                    return True

            # Default: idle or unknown state → OFF
            return False

        except (AttributeError, KeyError, TypeError):
            _LOGGER.warning("Could not extract charger status from coordinator data")
            return None

    @property
    def available(self) -> bool:
        """Return if entity is available."""
        return self.coordinator.last_update_success

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn on charging."""
        if not self._vehicle_id:
            _LOGGER.error("Vehicle ID not available for starting charge")
            return

        try:
            await self._api.async_start_charge(self._user_id, self._charger_id, self._vehicle_id)
            _LOGGER.info("Charging started successfully")
            # Refresh coordinator data to get updated state
            await self.coordinator.async_refresh()
        except Exception as err:
            _LOGGER.error("Failed to start charging: %s", err)
            raise

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn off charging."""
        try:
            await self._api.async_stop_charge(self._user_id, self._charger_id)
            _LOGGER.info("Charging stopped successfully")
            # Refresh coordinator data to get updated state
            await self.coordinator.async_refresh()
        except Exception as err:
            _LOGGER.error("Failed to stop charging: %s", err)
            raise
