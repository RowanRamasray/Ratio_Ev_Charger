"""API client for Ratio EV Charger charger integration using Cognito JWT Bearer tokens."""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any, Dict, Optional

from aiohttp.client_reqrep import ClientResponse as _ClientResponse
from aiohttp import ClientSession
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .auth import CognitoAuthManager

_LOGGER = logging.getLogger(__name__)

BASE_URL = "https://8q4y72fwo3.execute-api.eu-west-1.amazonaws.com"


class RatioAPI:
    """Ratio EV Charger HTTP API wrapper."""

    def __init__(self, hass: HomeAssistant, auth: CognitoAuthManager) -> None:
        self.hass = hass
        self._auth = auth

    async def async_request(
        self,
        method: str,
        url: str,
        json_data: Optional[Dict[str, Any]] = None,
        params: Optional[Dict[str, Any]] = None,
    ) -> Any:
        """Make a signed request to the Ratio API."""
        access_token = await self._auth.get_access_token()

        headers: Dict[str, str] = {
            "Content-Type": "application/json",
            "Accept": "*/*",
            "Cache-Control": "no-store",
            "User-Agent": "RatioEvApp/3.9.1",
            "Authorization": f"Bearer {access_token}",
        }

        body = None
        if json_data is not None:
            body = json.dumps(json_data).encode("utf-8")

        _LOGGER.debug("API request: %s %s", method, url)

        session: ClientSession = async_get_clientsession(self.hass)

        try:
            async with session.request(
                method, url, headers=headers, params=params, data=body
            ) as resp:
                return await self._handle_response(resp, method, url, headers)
        except Exception as err:  # pragma: no cover
            _LOGGER.error("API request error: %s %s - %s", method, url, err)
            raise

    async def _handle_response(
        self, resp: _ClientResponse, method: str, url: str, headers: Dict[str, Any]
    ) -> Any:
        response_text = await resp.text()
        if not resp.ok:
            _LOGGER.error(
                "API request failed: %s %s -> %d\nResponse: %s",
                method,
                url,
                resp.status,
                response_text[:500],
            )
            resp.raise_for_status()

        if not response_text:
            return None
        return json.loads(response_text)

    async def async_get_chargers(self, user_id: str) -> Dict[str, Any]:
        """Get overview of all chargers for the user."""
        url = f"{BASE_URL}/prod/users/{user_id}/chargers/status"
        return await self.async_request("GET", url, params={"id": "overview"})

    async def async_get_status(self, user_id: str, charger_id: str) -> Dict[str, Any]:
        """Get current status of a specific charger."""
        _LOGGER.debug("Getting charger status for charger_id=%s", charger_id)
        url = f"{BASE_URL}/prod/users/{user_id}/chargers/{charger_id}/status"
        return await self.async_request("GET", url, params={"id": "overview"})

    async def async_get_vehicles(self, user_id: str) -> Dict[str, Any]:
        """Get list of vehicles for the user."""
        _LOGGER.debug("Getting vehicles for user_id=%s", user_id)
        url = f"{BASE_URL}/prod/users/{user_id}/vehicles"
        return await self.async_request("GET", url)

    async def async_start_charge(
        self, user_id: str, charger_id: str, vehicle_id: str
    ) -> Dict[str, Any]:
        """Start charging."""
        _LOGGER.info("Starting charge on charger_id=%s with vehicle_id=%s", charger_id, vehicle_id)
        url = f"{BASE_URL}/prod/users/{user_id}/chargers/{charger_id}/command"
        body = {
            "startCommandParameters": {"vehicleId": vehicle_id},
            "transactionId": str(uuid.uuid4())[:16],
        }
        return await self.async_request("PUT", url, json_data=body, params={"id": "start-charge"})

    async def async_stop_charge(self, user_id: str, charger_id: str) -> Dict[str, Any]:
        """Stop charging."""
        _LOGGER.info("Stopping charge on charger_id=%s", charger_id)
        url = f"{BASE_URL}/prod/users/{user_id}/chargers/{charger_id}/command"
        body = {"transactionId": str(uuid.uuid4())[:16]}
        return await self.async_request("PUT", url, json_data=body, params={"id": "stop-charge"})

    async def async_set_charging_mode(
        self, user_id: str, charger_id: str, body: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Set the charging mode."""
        _LOGGER.info("Setting charging mode on charger_id=%s", charger_id)
        url = f"{BASE_URL}/prod/users/{user_id}/chargers/{charger_id}/settings"
        return await self.async_request("PUT", url, json_data=body, params={"id": "user"})

    async def async_get_user_settings(self, user_id: str, charger_id: str) -> Dict[str, Any]:
        """Get user settings for the given charger."""
        _LOGGER.debug("Getting user settings for charger_id=%s", charger_id)
        url = f"{BASE_URL}/prod/users/{user_id}/chargers/{charger_id}/settings"
        return await self.async_request("GET", url, params={"id": "user"})
