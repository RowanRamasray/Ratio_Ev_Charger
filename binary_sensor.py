"""Binary sensor entities for Ratio EV Charger integration."""

from __future__ import annotations

import logging

from homeassistant.components.binary_sensor import (
    BinarySensorEntity,
    BinarySensorDeviceClass,
)
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .coordinator import ChargerCoordinator
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up binary sensor entities."""
    data = hass.data[DOMAIN][entry.entry_id]
    coordinator: ChargerCoordinator = data["coordinator"]
    charger_id: str = data["charger_id"]

    entities = [
        RatioVehicleConnectedBinarySensor(coordinator, charger_id),
        RatioChargingAuthorizedBinarySensor(coordinator, charger_id),
        RatioChargerErrorBinarySensor(coordinator, charger_id),
    ]

    async_add_entities(entities)


class RatioVehicleConnectedBinarySensor(CoordinatorEntity, BinarySensorEntity):
    """Binary sensor for vehicle connection state."""

    def __init__(self, coordinator: ChargerCoordinator, charger_id: str) -> None:
        """Initialize the binary sensor."""
        super().__init__(coordinator)
        self._charger_id = charger_id
        self._attr_unique_id = f"{coordinator.charger_id}_vehicle_connected"
        self._attr_name = "Vehicle Connected"
        self._attr_device_class = BinarySensorDeviceClass.PLUG
        self._attr_icon = "mdi:car-electric"

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
        """Return if vehicle is connected."""
        if self.coordinator.data is None:
            return None

        try:
            data = self.coordinator.data
            status = data.get("status", data)
            return (
                status.get("chargerStatus", {}).get("indicators", {}).get("isVehicleConnected")
            )
        except (AttributeError, KeyError, TypeError):
            _LOGGER.warning("Could not extract vehicle connection state from coordinator data")
            return None

    @property
    def available(self) -> bool:
        """Return if entity is available."""
        return self.coordinator.last_update_success


class RatioChargingAuthorizedBinarySensor(CoordinatorEntity, BinarySensorEntity):
    """Binary sensor for charging authorization state."""

    def __init__(self, coordinator: ChargerCoordinator, charger_id: str) -> None:
        """Initialize the binary sensor."""
        super().__init__(coordinator)
        self._charger_id = charger_id
        self._attr_unique_id = f"{coordinator.charger_id}_charging_authorized"
        self._attr_name = "Charging Authorized"
        self._attr_icon = "mdi:badge-account"

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
        """Return if charging is authorized."""
        if self.coordinator.data is None:
            return None

        try:
            data = self.coordinator.data
            status = data.get("status", data)
            return (
                status.get("chargerStatus", {}).get("indicators", {}).get("isChargingAuthorized")
            )
        except (AttributeError, KeyError, TypeError):
            _LOGGER.warning("Could not extract isChargingAuthorized from coordinator data")
            return None

    @property
    def available(self) -> bool:
        """Return if entity is available."""
        return self.coordinator.last_update_success


class RatioChargerErrorBinarySensor(CoordinatorEntity, BinarySensorEntity):
    """Binary sensor for charger error state."""

    def __init__(self, coordinator: ChargerCoordinator, charger_id: str) -> None:
        """Initialize the binary sensor."""
        super().__init__(coordinator)
        self._charger_id = charger_id
        self._attr_unique_id = f"{coordinator.charger_id}_charger_error"
        self._attr_name = "Charger Error"
        self._attr_device_class = BinarySensorDeviceClass.PROBLEM
        self._attr_icon = "mdi:alert-circle"

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
        """Return if charger is in error."""
        if self.coordinator.data is None:
            return None

        try:
            data = self.coordinator.data
            status = data.get("status", data)
            indicators = status.get("chargerStatus", {}).get("indicators", {})
            charging_state = indicators.get("chargingState")
            errors = indicators.get("errors") or []

            if isinstance(charging_state, str) and charging_state.strip().lower() == "error":
                return True
            if isinstance(errors, (list, tuple)) and len(errors) > 0:
                return True

            return False
        except (AttributeError, KeyError, TypeError):
            _LOGGER.warning("Could not extract error state from coordinator data")
            return None

    @property
    def available(self) -> bool:
        """Return if entity is available."""
        return self.coordinator.last_update_success
