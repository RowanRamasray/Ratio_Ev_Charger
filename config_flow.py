"""Config flow for Ratio EV Charger integration."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.config_entries import ConfigEntry
from homeassistant.data_entry_flow import FlowResult

from .api import RatioAPI
from .auth import CognitoAuthManager
from .const import (
    DEFAULT_CLIENT_ID,
    DEFAULT_IDENTITY_POOL_ID,
    DEFAULT_REGION,
    DEFAULT_USER_POOL_ID,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)


class RatioEVConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Ratio EV Charger."""

    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Handle the initial step."""
        errors: dict[str, str] = {}

        if user_input is not None:
            await self.async_set_unique_id(user_input["username"].lower())
            self._abort_if_unique_id_configured()

            auth = CognitoAuthManager(
                self.hass,
                username=user_input["username"],
                password=user_input["password"],
                client_id=DEFAULT_CLIENT_ID,
                user_pool_id=DEFAULT_USER_POOL_ID,
                identity_pool_id=DEFAULT_IDENTITY_POOL_ID,
                region=DEFAULT_REGION,
            )

            try:
                await auth.async_full_login()
            except Exception as err:  # pragma: no cover
                _LOGGER.debug("Login failed: %s", err, exc_info=True)
                errors["base"] = "auth_failed"
            else:
                # DeviceKey is required for optimized token refresh with GetTokensFromRefreshToken API
                # It comes from Cognito's NewDeviceMetadata during authentication.
                if not auth.device_key:
                    _LOGGER.warning(
                        "DeviceKey not available from Cognito authentication. "
                        "Token refresh will use full login (SRP) instead of device token refresh. "
                        "This may happen if: (1) Device tracking is not enabled on your Cognito User Pool, "
                        "(2) Different Client IDs between iOS and Home Assistant with different settings, or "
                        "(3) Cognito User Pool doesn't return DeviceKey for your configuration. "
                        "Tokens ARE available (Access: %s, ID: %s, Refresh: %s), allowing setup to continue. "
                        "See: https://docs.aws.amazon.com/cognito/latest/developerguide/amazon-cognito-user-pools-device-tracking.html",
                        "present" if auth.access_token else "missing",
                        "present" if auth.id_token else "missing",
                        "present" if auth.refresh_token else "missing",
                    )
                else:
                    _LOGGER.info("DeviceKey available for optimized token refresh: %s...", auth.device_key[:20])

                # Continue with setup regardless of whether DeviceKey is available
                api = RatioAPI(self.hass, auth)

                try:
                    user_id = await auth.get_user_id()
                    chargers = await api.async_get_chargers(user_id)
                    charger_id = self._extract_charger_id(chargers)
                    vehicles = await api.async_get_vehicles(user_id)
                    vehicle_id = self._extract_vehicle_id(vehicles)

                    entry_data: dict[str, Any] = {
                        "username": user_input["username"],
                        "password": user_input["password"],
                        "client_id": DEFAULT_CLIENT_ID,
                        "user_pool_id": DEFAULT_USER_POOL_ID,
                        "identity_pool_id": DEFAULT_IDENTITY_POOL_ID,
                        "region": DEFAULT_REGION,
                        "user_id": user_id,
                        "charger_id": charger_id,
                        "vehicle_id": vehicle_id,
                        "access_token": auth.access_token,
                        "id_token": auth.id_token,
                        "refresh_token": auth.refresh_token,
                        "device_key": auth.device_key,
                        "device_group_key": auth._device_group_key,
                        "device_password": auth._device_password,
                        "token_expires": auth.token_expires,
                    }

                    return self.async_create_entry(title="Ratio EV Charger", data=entry_data)
                except Exception as err:  # pragma: no cover
                    _LOGGER.exception("Discovery failed: %s", err)
                    errors["base"] = "discovery_failed"

        return self.async_show_form(step_id="user", data_schema=self._schema(), errors=errors)

    @staticmethod
    def _extract_charger_id(chargers: dict[str, Any]) -> str:
        """Extract a stable charger identifier from the API response."""
        items = chargers.get("chargers") or []
        if not items or not isinstance(items, list):
            raise ValueError("no_chargers")

        charger = items[0]
        for key in ("serialNumber", "id", "chargerId", "serial"):
            if isinstance(charger, dict) and charger.get(key):
                return charger[key]

        raise ValueError("no_chargers")

    @staticmethod
    def _extract_vehicle_id(vehicles: dict[str, Any]) -> str:
        """Extract a vehicle identifier from the API response."""
        items = vehicles.get("vehicles") or []
        if not items or not isinstance(items, list):
            raise ValueError("no_vehicles")

        vehicle = items[0]
        for key in ("id", "vehicleId"):
            if isinstance(vehicle, dict) and vehicle.get(key):
                return vehicle[key]

        raise ValueError("no_vehicles")

    def _schema(self) -> vol.Schema:
        return vol.Schema(
            {
                vol.Required("username"): str,
                vol.Required("password"): str,
            }
        )
