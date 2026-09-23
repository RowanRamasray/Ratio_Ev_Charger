"""Config flow for Ratio EV Charger integration."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.data_entry_flow import AbortFlow, FlowResult

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

    VERSION = 2

    def __init__(self) -> None:
        """Initialize the config flow."""
        self._entry_data: dict[str, Any] | None = None
        self._charger_options: dict[str, str] = {}

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Handle the initial step."""
        errors: dict[str, str] = {}

        if user_input is not None:
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
                    charger_options = self._extract_charger_options(chargers)
                    vehicles = await api.async_get_vehicles(user_id)
                    vehicle_id = self._extract_vehicle_id(vehicles)

                    self._entry_data = {
                        "username": user_input["username"],
                        "password": user_input["password"],
                        "client_id": DEFAULT_CLIENT_ID,
                        "user_pool_id": DEFAULT_USER_POOL_ID,
                        "identity_pool_id": DEFAULT_IDENTITY_POOL_ID,
                        "region": DEFAULT_REGION,
                        "user_id": user_id,
                        "vehicle_id": vehicle_id,
                        "access_token": auth.access_token,
                        "id_token": auth.id_token,
                        "refresh_token": auth.refresh_token,
                        "device_key": auth.device_key,
                        "device_group_key": auth._device_group_key,
                        "device_password": auth._device_password,
                        "token_expires": auth.token_expires,
                    }

                    if len(charger_options) == 1:
                        charger_id = next(iter(charger_options))
                        return await self._async_create_charger_entry(
                            charger_id, charger_options[charger_id]
                        )

                    self._charger_options = charger_options
                    return await self.async_step_charger()
                except AbortFlow:
                    raise
                except Exception as err:  # pragma: no cover
                    _LOGGER.exception("Discovery failed: %s", err)
                    errors["base"] = "discovery_failed"

        return self.async_show_form(step_id="user", data_schema=self._schema(), errors=errors)

    async def async_step_charger(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Let the user choose a charger when the account has multiple chargers."""
        if self._entry_data is None or not self._charger_options:
            return self.async_abort(reason="discovery_failed")

        if user_input is not None:
            charger_id = user_input["charger_id"]
            return await self._async_create_charger_entry(
                charger_id, self._charger_options[charger_id]
            )

        return self.async_show_form(
            step_id="charger",
            data_schema=vol.Schema(
                {vol.Required("charger_id"): vol.In(self._charger_options)}
            ),
        )

    async def _async_create_charger_entry(
        self, charger_id: str, charger_label: str
    ) -> FlowResult:
        """Create an entry for a selected charger."""
        assert self._entry_data is not None

        await self.async_set_unique_id(charger_id)
        self._abort_if_unique_id_configured()

        # Version 1 entries used the account username as their unique ID. Check
        # their stored charger ID as well so they cannot be added a second time.
        if any(
            str(entry.data.get("charger_id")) == charger_id
            for entry in self.hass.config_entries.async_entries(DOMAIN)
        ):
            return self.async_abort(reason="already_configured")

        return self.async_create_entry(
            title=f"Ratio EV Charger - {charger_label}",
            data={**self._entry_data, "charger_id": charger_id},
        )

    @classmethod
    def _extract_charger_options(cls, chargers: dict[str, Any]) -> dict[str, str]:
        """Extract charger IDs and useful display labels from an API response."""
        items = chargers.get("chargers") or []
        if not isinstance(items, list):
            raise ValueError("no_chargers")

        options: dict[str, str] = {}
        for charger in items:
            if not isinstance(charger, dict):
                continue
            try:
                charger_id = cls._extract_charger_id(charger)
            except ValueError:
                continue
            options[charger_id] = cls._extract_charger_label(charger, charger_id)

        if not options:
            raise ValueError("no_chargers")

        return options

    @staticmethod
    def _extract_charger_id(charger: dict[str, Any]) -> str:
        """Extract a stable identifier from one charger record."""
        for key in ("serialNumber", "id", "chargerId", "serial"):
            if value := charger.get(key):
                return str(value)

        raise ValueError("no_chargers")

    @staticmethod
    def _extract_charger_label(charger: dict[str, Any], charger_id: str) -> str:
        """Return the best human-readable label available for a charger."""
        for key in (
            "displayName",
            "name",
            "chargerName",
            "customName",
            "nickname",
            "label",
        ):
            value = charger.get(key)
            if isinstance(value, str) and value.strip():
                name = value.strip()
                return name if name == charger_id else f"{name} ({charger_id})"

        return charger_id

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
