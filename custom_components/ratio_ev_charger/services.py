"""Service implementations for Ratio EV Charger charger."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError

from .api import RatioAPI
from .const import DOMAIN, SERVICE_START_CHARGE, SERVICE_STOP_CHARGE

_LOGGER = logging.getLogger(__name__)

SERVICE_SCHEMA_START = vol.Schema(
    {
        vol.Required("charger_id"): str,
    }
)

SERVICE_SCHEMA_STOP = vol.Schema(
    {
        vol.Required("charger_id"): str,
    }
)


async def async_setup_services(hass: HomeAssistant) -> None:
    """Set up services for the integration."""

    async def handle_start_charge(call: ServiceCall) -> None:
        """Handle the start_charge service call."""
        charger_id = call.data.get("charger_id")

        try:
            # Get the API instance and configuration from the first entry
            entries = hass.config_entries.async_entries(DOMAIN)
            if not entries:
                raise HomeAssistantError("No Ratio EV Charger integration configured")

            entry = entries[0]
            api: RatioAPI = hass.data[DOMAIN][entry.entry_id]["api"]
            user_id = entry.data.get("user_id")
            vehicle_id = hass.data[DOMAIN][entry.entry_id].get("vehicle_id")

            if not user_id or not vehicle_id:
                raise HomeAssistantError("Missing user_id or vehicle_id")

            await api.async_start_charge(user_id, charger_id, vehicle_id)
            _LOGGER.info("Started charging on charger %s", charger_id)
        except Exception as err:
            _LOGGER.error("Error starting charge: %s", err)
            raise HomeAssistantError(f"Error starting charge: {err}") from err

    async def handle_stop_charge(call: ServiceCall) -> None:
        """Handle the stop_charge service call."""
        charger_id = call.data.get("charger_id")

        try:
            # Get the API instance and configuration from the first entry
            entries = hass.config_entries.async_entries(DOMAIN)
            if not entries:
                raise HomeAssistantError("No Ratio EV Charger integration configured")

            entry = entries[0]
            api: RatioAPI = hass.data[DOMAIN][entry.entry_id]["api"]
            user_id = entry.data.get("user_id")

            if not user_id:
                raise HomeAssistantError("Missing user_id")

            await api.async_stop_charge(user_id, charger_id)
            _LOGGER.info("Stopped charging on charger %s", charger_id)
        except Exception as err:
            _LOGGER.error("Error stopping charge: %s", err)
            raise HomeAssistantError(f"Error stopping charge: {err}") from err

    hass.services.async_register(
        DOMAIN,
        SERVICE_START_CHARGE,
        handle_start_charge,
        schema=SERVICE_SCHEMA_START,
    )

    hass.services.async_register(
        DOMAIN,
        SERVICE_STOP_CHARGE,
        handle_stop_charge,
        schema=SERVICE_SCHEMA_STOP,
    )

    _LOGGER.debug("Services registered for %s", DOMAIN)
