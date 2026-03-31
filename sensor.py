"""Sensors for Ratio EV Charger charger."""

from __future__ import annotations

import logging

from homeassistant.components.sensor import (
    SensorEntity,
    SensorStateClass,
    SensorDeviceClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    UnitOfPower,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.helpers.device_registry import DeviceInfo

from .coordinator import ChargerCoordinator
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up sensors from a config entry."""
    data = hass.data[DOMAIN][entry.entry_id]
    coordinator: ChargerCoordinator = data["coordinator"]
    charger_id: str = data["charger_id"]

    sensors = [
        RatioChargingPowerSensor(coordinator, charger_id),
        RatioChargerStatusSensor(coordinator, charger_id),
    ]

    async_add_entities(sensors)


class RatioChargingPowerSensor(CoordinatorEntity, SensorEntity):
    """Sensor for current charging power."""

    def __init__(self, coordinator: ChargerCoordinator, charger_id: str) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self._charger_id = charger_id
        self._attr_unique_id = f"{coordinator.charger_id}_charging_power"
        self._attr_name = "Charging Power"
        self._attr_device_class = SensorDeviceClass.POWER
        self._attr_state_class = SensorStateClass.MEASUREMENT
        self._attr_native_unit_of_measurement = UnitOfPower.WATT
        self._attr_icon = "mdi:lightning-bolt"

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
    def native_value(self) -> int | None:
        """Return the current charging power."""
        if self.coordinator.data is None:
            return None

        try:
            data = self.coordinator.data
            status = data.get("status", data)
            return status.get("chargeSessionStatus", {}).get("actualChargingPower")
        except (AttributeError, KeyError, TypeError):
            _LOGGER.warning("Could not extract charging power from coordinator data")
            return None

    @property
    def available(self) -> bool:
        """Return if entity is available."""
        return self.coordinator.last_update_success


class RatioChargerStatusSensor(CoordinatorEntity, SensorEntity):
    """Primary sensor representing the normalized charger status."""

    def __init__(self, coordinator: ChargerCoordinator, charger_id: str) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self._charger_id = charger_id
        self._attr_unique_id = f"{coordinator.charger_id}_charger_status"
        self._attr_name = "Charger Status"
        self._attr_icon = "mdi:ev-station"

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
    def native_value(self) -> str | None:
        """Return the normalized charger status state."""
        if self.coordinator.data is None:
            return None

        data = self.coordinator.data
        status = data.get("status", data)
        indicators = status.get("chargerStatus", {}).get("indicators", {})
        charging_state = indicators.get("chargingState")
        is_paused = indicators.get("isChargingPaused")
        is_connected = indicators.get("isVehicleConnected")
        is_authorized = indicators.get("isChargingAuthorized")
        errors = indicators.get("errors") or []

        # Error overrides all other states
        if isinstance(charging_state, str) and charging_state.strip().lower() == "error":
            return "error"
        if isinstance(errors, (list, tuple)) and len(errors) > 0:
            return "error"

        if is_authorized is False:
            return "unauthorized"

        if is_paused is True:
            return "paused"

        if is_connected is False:
            return "waiting_for_vehicle"

        if isinstance(charging_state, str):
            cs = charging_state.strip().lower()
            if cs == "charging":
                return "charging"
            # Map vehicle detected state to a simple idle state
            if cs == "vehicledetected" or cs == "vehicle_detected" or cs == "vehicle detected":
                return "idle"

        # Vehicle connected but not charging and not paused
        return "idle"

    @property
    def available(self) -> bool:
        """Return if entity is available."""
        return self.coordinator.last_update_success

