"""Ratio EV Charger integration package."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.const import Platform
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers import device_registry as dr

from .auth import CognitoAuthManager
from .api import RatioAPI
from .coordinator import ChargerCoordinator
from .services import async_setup_services
from .const import (
    DEFAULT_CLIENT_ID,
    DEFAULT_IDENTITY_POOL_ID,
    DEFAULT_REGION,
    DEFAULT_USER_POOL_ID,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [Platform.SENSOR, Platform.BINARY_SENSOR, Platform.SWITCH, Platform.SELECT]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Ratio EV Charger from a config entry."""
    hass.data.setdefault(DOMAIN, {})

    username = entry.data.get("username")
    password = entry.data.get("password")
    client_id = entry.data.get("client_id", DEFAULT_CLIENT_ID)
    user_pool_id = entry.data.get("user_pool_id", DEFAULT_USER_POOL_ID)
    identity_pool_id = entry.data.get("identity_pool_id", DEFAULT_IDENTITY_POOL_ID)
    region = entry.data.get("region", DEFAULT_REGION)

    if not all([username, password, client_id, user_pool_id, identity_pool_id]):
        _LOGGER.error("Missing credentials or Cognito configuration in config entry")
        raise ConfigEntryAuthFailed("Missing credentials")

    auth_manager = CognitoAuthManager(
        hass,
        username=username,
        password=password,
        client_id=client_id,
        user_pool_id=user_pool_id,
        identity_pool_id=identity_pool_id,
        region=region,
        config_entry=entry,
        access_token=entry.data.get("access_token"),
        id_token=entry.data.get("id_token"),
        refresh_token=entry.data.get("refresh_token"),
        device_key=entry.data.get("device_key"),
        device_group_key=entry.data.get("device_group_key"),
        device_password=entry.data.get("device_password"),
        token_expires=entry.data.get("token_expires"),
    )

    try:
        await auth_manager.ensure_valid_token()
    except Exception as err:  # pragma: no cover
        _LOGGER.exception("Authentication failed during setup: %s", err)
        raise ConfigEntryAuthFailed("Authentication failed") from err

    api = RatioAPI(hass, auth_manager)

    coordinator = ChargerCoordinator(
        hass,
        api,
        entry.data["user_id"],
        entry.data["charger_id"],
        update_interval=60,
    )

    try:
        await coordinator.async_config_entry_first_refresh()
    except Exception as err:  # pragma: no cover
        _LOGGER.error("Failed to fetch initial data: %s", err)
        raise ConfigEntryNotReady from err

    charger_id = entry.data.get("charger_id")
    device_registry = dr.async_get(hass)
    device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, charger_id)},
        name="Ratio EV Charger",
        manufacturer="Ratio",
        model="EV Charger",
    )

    hass.data[DOMAIN][entry.entry_id] = {
        "auth": auth_manager,
        "api": api,
        "coordinator": coordinator,
        "user_id": entry.data.get("user_id"),
        "charger_id": charger_id,
        "vehicle_id": entry.data.get("vehicle_id"),
    }

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    await async_setup_services(hass)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id, None)

    return unload_ok
