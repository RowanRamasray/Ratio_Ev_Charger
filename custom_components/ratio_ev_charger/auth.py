"""Authentication helpers for Cognito using SRP + refresh tokens."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import os
import time
from datetime import datetime
from typing import Any, Dict, Optional, Tuple

import boto3
from botocore.exceptions import ClientError
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from warrant.aws_srp import AWSSRP

from .const import COGNITO_ISSUER

_LOGGER = logging.getLogger(__name__)

# SRP large prime N (same as warrant/aws_srp.py and amazon-cognito-identity-js)
_N_HEX = (
    'FFFFFFFFFFFFFFFFC90FDAA22168C234C4C6628B80DC1CD1'
    '29024E088A67CC74020BBEA63B139B22514A08798E3404DD'
    'EF9519B3CD3A431B302B0A6DF25F14374FE1356D6D51C245'
    'E485B576625E7EC6F44C42E9A637ED6B0BFF5CB6F406B7ED'
    'EE386BFB5A899FA5AE9F24117C4B1FE649286651ECE45B3D'
    'C2007CB8A163BF0598DA48361C55D39A69163FA8FD24CF5F'
    '83655D23DCA3AD961C62F356208552BB9ED529077096966D'
    '670C354E4ABC9804F1746C08CA18217C32905E462E36CE3B'
    'E39E772C180E86039B2783A2EC07A28FB5C55DF06F4C52C9'
    'DE2BCBF6955817183995497CEA956AE515D2261898FA0510'
    '15728E5A8AAAC42DAD33170D04507A33A85521ABDF1CBA64'
    'ECFB850458DBEF0A8AEA71575D060C7DB3970F85A6E1E4C7'
    'ABF5AE8CDB0933D71E8C94E04A25619DCEE3D2261AD2EE6B'
    'F12FFA06D98A0864D87602733EC86A64521F2B18177B200C'
    'BBE117577A615D6C770988C0BAD946E208E24FA074E5AB31'
    '43DB5BFCE0FD108E4B82D120A93AD2CAFFFFFFFFFFFFFFFF'
)
_G_HEX = '2'


def _pad_hex_srp(hex_str: str) -> str:
    """Pad hex string for SRP math (ensure positive interpretation)."""
    if len(hex_str) % 2 == 1:
        hex_str = '0' + hex_str
    elif hex_str[0] in '89ABCDEFabcdef':
        hex_str = '00' + hex_str
    return hex_str


def _generate_device_verifier(
    device_group_key: str, device_key: str
) -> Tuple[str, str, str]:
    """Generate device SRP verifier for ConfirmDevice API.

    This replicates the generateHashDevice logic from amazon-cognito-identity-js.
    The verifier allows Cognito to verify the device in future DEVICE_SRP_AUTH flows
    and is required for device confirmation.

    Returns:
        (random_password, salt_base64, verifier_base64)
    """
    # 1. Generate a random password (same approach as JS SDK generateRandomString)
    random_password = base64.standard_b64encode(os.urandom(40)).decode('utf-8')

    # 2. Hash the combined device identifiers with the random password
    #    combined = DeviceGroupKey + DeviceKey + ":" + randomPassword
    combined = f"{device_group_key}{device_key}:{random_password}"
    password_hash = hashlib.sha256(combined.encode('utf-8')).hexdigest()

    # 3. Generate random salt (16 bytes)
    salt_hex = os.urandom(16).hex()
    padded_salt = _pad_hex_srp(salt_hex)

    # 4. Compute x = SHA256(padded_salt_bytes + password_hash)
    x_hex = hashlib.sha256(bytes.fromhex(padded_salt + password_hash)).hexdigest()
    x = int(x_hex, 16)

    # 5. Compute verifier = g^x mod N
    g = int(_G_HEX, 16)
    n = int(_N_HEX, 16)
    verifier = pow(g, x, n)

    # 6. Encode salt and verifier as base64 for the ConfirmDevice API
    verifier_hex = _pad_hex_srp(format(verifier, 'x'))
    salt_b64 = base64.standard_b64encode(bytes.fromhex(padded_salt)).decode('utf-8')
    verifier_b64 = base64.standard_b64encode(bytes.fromhex(verifier_hex)).decode('utf-8')

    return random_password, salt_b64, verifier_b64


def decode_token_payload(token: str) -> Dict[str, Any]:
    """Decode JWT payload (no verification)."""

    try:
        payload = token.split(".")[1]
        padding = "=" * (-len(payload) % 4)
        decoded = base64.urlsafe_b64decode(payload + padding)
        return json.loads(decoded)
    except Exception:
        return {}


def _parse_cognito_response(response: Dict[str, Any]) -> Dict[str, Any]:
    """Extract the tokens and device key from a Cognito response.

    Cognito returns tokens in AuthenticationResult and device metadata in NewDeviceMetadata.
    NewDeviceMetadata can be at the top level OR nested inside AuthenticationResult.

    Structure 1 (top-level):
    {
        "AuthenticationResult": {"AccessToken": "...", "IdToken": "...", "RefreshToken": "..."},
        "NewDeviceMetadata": {"DeviceKey": "eu-west-1_uuid", "DeviceGroupKey": "..."}
    }

    Structure 2 (nested in AuthenticationResult - returned by RespondToAuthChallenge):
    {
        "AuthenticationResult": {
            "AccessToken": "...",
            "IdToken": "...",
            "RefreshToken": "...",
            "NewDeviceMetadata": {"DeviceKey": "eu-west-1_uuid", "DeviceGroupKey": "..."},
            "TokenType": "Bearer"
        },
        "ChallengeParameters": {}
    }
    """

    auth = {}

    # Extract tokens from AuthenticationResult
    if "AuthenticationResult" in response and isinstance(response["AuthenticationResult"], dict):
        auth_result = response["AuthenticationResult"]
        # Copy all keys except nested NewDeviceMetadata (we'll handle that separately)
        for key, value in auth_result.items():
            if key != "NewDeviceMetadata":
                auth[key] = value

        # Check for NewDeviceMetadata NESTED inside AuthenticationResult
        if "NewDeviceMetadata" in auth_result and isinstance(auth_result["NewDeviceMetadata"], dict):
            new_device_meta = auth_result["NewDeviceMetadata"]
            if "DeviceKey" in new_device_meta:
                auth["DeviceKey"] = new_device_meta["DeviceKey"]
            if "DeviceGroupKey" in new_device_meta:
                auth["DeviceGroupKey"] = new_device_meta["DeviceGroupKey"]

    # Extract DeviceKey from NewDeviceMetadata at top level (fallback)
    if "NewDeviceMetadata" in response and isinstance(response["NewDeviceMetadata"], dict):
        new_device_meta = response["NewDeviceMetadata"]
        if "DeviceKey" in new_device_meta:
            auth["DeviceKey"] = new_device_meta["DeviceKey"]
        if "DeviceGroupKey" in new_device_meta:
            auth["DeviceGroupKey"] = new_device_meta["DeviceGroupKey"]

    # Fallback: DeviceKey at the top level
    if "DeviceKey" in response and "DeviceKey" not in auth:
        auth["DeviceKey"] = response["DeviceKey"]

    _LOGGER.debug("Parsed Cognito response - keys present: %s", list(auth.keys()))

    # WARNING: If no DeviceKey found, log all top-level response keys for debugging
    if not auth.get("DeviceKey"):
        response_keys = list(response.keys()) if isinstance(response, dict) else "N/A"
        _LOGGER.debug(
            "DeviceKey not found in Cognito response. "
            "Top-level response keys: %s. "
            "NewDeviceMetadata present: %s. "
            "This indicates device tracking may not be enabled on your Cognito User Pool.",
            response_keys,
            "NewDeviceMetadata" in response,
        )

    return auth


class CognitoAuthManager:
    """Handles Cognito authentication, token refresh and persistence."""

    def __init__(
        self,
        hass: HomeAssistant,
        username: str,
        password: str,
        client_id: str,
        user_pool_id: str,
        identity_pool_id: str,
        region: str = "eu-west-1",
        config_entry: Optional[ConfigEntry] = None,
        access_token: Optional[str] = None,
        id_token: Optional[str] = None,
        refresh_token: Optional[str] = None,
        device_key: Optional[str] = None,
        device_group_key: Optional[str] = None,
        device_password: Optional[str] = None,
        token_expires: Optional[float] = None,
    ):
        self.hass = hass
        self.config_entry = config_entry

        self.username = username
        self.password = password

        self.client_id = client_id
        self.user_pool_id = user_pool_id
        self.identity_pool_id = identity_pool_id
        self.region = region

        self._access_token: Optional[str] = access_token
        self._id_token: Optional[str] = id_token
        self._refresh_token: Optional[str] = refresh_token
        self._device_key: Optional[str] = device_key
        self._device_group_key: Optional[str] = device_group_key
        self._device_password: Optional[str] = device_password

        self._token_expiration: float = token_expires or 0
        self._cached_credentials: Optional[Dict[str, Any]] = None

        # Lock to prevent concurrent refresh attempts
        self._refresh_lock = asyncio.Lock()

        if self._access_token and not self._token_expiration:
            self._token_expiration = self._get_expiration_from_token(self._access_token)

    @property
    def access_token(self) -> Optional[str]:
        return self._access_token

    @property
    def id_token(self) -> Optional[str]:
        return self._id_token

    @property
    def refresh_token(self) -> Optional[str]:
        return self._refresh_token

    @property
    def device_key(self) -> Optional[str]:
        return self._device_key

    @property
    def token_expires(self) -> float:
        return self._token_expiration

    def _get_expiration_from_token(self, token: str) -> float:
        payload = decode_token_payload(token)
        return payload.get("exp", time.time() + 3600)

    def _validate_refresh_token(self) -> bool:
        """Validate that refresh token looks valid (basic format check).

        Returns True if token appears valid, False otherwise.
        Logs detailed warning if token format looks corrupted.
        """
        if not self._refresh_token:
            return False

        # Refresh tokens can be JWE (3 parts: header.encrypted.auth_tag) or opaque strings
        # At minimum, they should be non-empty and reasonably sized
        if len(self._refresh_token) < 50:
            _LOGGER.warning(
                "Refresh token appears unusually short (%d chars). "
                "May be corrupted. Token starts with: %s",
                len(self._refresh_token),
                self._refresh_token[:20] if len(self._refresh_token) >= 20 else self._refresh_token
            )
            return False

        # Check for basic JWE structure if it looks like JWT (has dots)
        if "." in self._refresh_token:
            parts = self._refresh_token.split(".")
            if len(parts) == 3:
                # Valid JWE format (header.encrypted_payload.auth_tag)
                return True
            elif len(parts) == 5:
                # Valid JWE with extra segments
                return True
            else:
                _LOGGER.warning(
                    "Refresh token has invalid JWT structure (%d parts, expected 3 or 5). "
                    "Token may be corrupted.",
                    len(parts)
                )
                return False

        # Opaque token, just check it's not obviously wrong
        return True

    async def _persist_tokens(self) -> None:
        """Persist tokens back to the config entry."""
        if not self.config_entry:
            return

        data = dict(self.config_entry.data)
        updated = False

        new_values = {
            "access_token": self._access_token,
            "id_token": self._id_token,
            "refresh_token": self._refresh_token,
            "device_key": self._device_key,
            "device_group_key": self._device_group_key,
            "device_password": self._device_password,
            "token_expires": self._token_expiration,
        }

        for key, value in new_values.items():
            if value is not None and data.get(key) != value:
                data[key] = value
                updated = True

        if updated:
            _LOGGER.debug("Persisting updated tokens to config entry")
            self.hass.config_entries.async_update_entry(self.config_entry, data=data)

    async def _apply_tokens(self, tokens: Dict[str, Any]) -> None:
        """Apply token values from Cognito response."""
        # Update AccessToken and IdToken only when present — do not wipe existing tokens
        access_token = tokens.get("AccessToken")
        if access_token:
            self._access_token = access_token
            # Update expiration based on the new access token
            try:
                self._token_expiration = self._get_expiration_from_token(self._access_token)
            except Exception:  # pragma: no cover - defensive
                _LOGGER.debug("Failed to parse access token expiration; keeping previous value")
        else:
            _LOGGER.debug("No AccessToken in Cognito response; keeping existing access token")

        id_token = tokens.get("IdToken")
        if id_token:
            self._id_token = id_token
        else:
            _LOGGER.debug("No IdToken in Cognito response; keeping existing id token")

        # Only replace the refresh token when Cognito explicitly returns one
        if "RefreshToken" in tokens and tokens.get("RefreshToken"):
            new_refresh = tokens["RefreshToken"]
            if new_refresh != self._refresh_token:
                _LOGGER.debug("Replacing refresh token from Cognito response")
            else:
                _LOGGER.debug("Received same refresh token from Cognito; no change")
            self._refresh_token = new_refresh
        else:
            if self._refresh_token:
                _LOGGER.debug("Reusing existing refresh token (not returned by Cognito)")
            else:
                _LOGGER.debug("No refresh token available")

        # Update device key and group key only when present
        device_key = tokens.get("DeviceKey")
        if device_key:
            self._device_key = device_key
            _LOGGER.debug("DeviceKey updated: %s", device_key)

        device_group_key = tokens.get("DeviceGroupKey")
        if device_group_key:
            self._device_group_key = device_group_key

        # Invalidate cached AWS credentials whenever tokens change
        self._cached_credentials = None
        await self._persist_tokens()

    def _is_token_expired(self) -> bool:
        """Check if the token is expiring soon (5 minute buffer)."""
        return time.time() >= (self._token_expiration - 300)



    async def async_full_login(self) -> None:
        """Perform SRP login using two-step flow (InitiateAuth + RespondToAuthChallenge).

        This matches the iOS app's SRP flow:
        1. InitiateAuth(USER_SRP_AUTH) → PASSWORD_VERIFIER challenge
        2. Use warrant's AWSSRP to compute SRP response
        3. RespondToAuthChallenge → Tokens + NewDeviceMetadata with DeviceKey
        4. ConfirmDevice → Registers device with SRP verifier so Cognito trusts it

        The ConfirmDevice step is CRITICAL: without it, Cognito will reject the DeviceKey
        during token refresh via GetTokensFromRefreshToken, returning "Invalid Refresh Token".

        Reference: https://docs.aws.amazon.com/cognito/latest/developerguide/amazon-cognito-user-pools-device-tracking.html
        """
        _LOGGER.info("Performing full SRP login (InitiateAuth + RespondToAuthChallenge)")

        def _sync() -> Dict[str, Any]:
            client = boto3.client("cognito-idp", region_name=self.region)

            aws = AWSSRP(
                username=self.username,
                password=self.password,
                pool_id=self.user_pool_id,
                client_id=self.client_id,
                client=client,
            )

            return aws.authenticate_user()

        response = await self.hass.async_add_executor_job(_sync)

        # DEBUG: Log raw response structure (redact sensitive tokens)
        _LOGGER.debug(
            "Raw Cognito SRP response structure: %s",
            {k: (v if k not in ["AccessToken", "IdToken", "RefreshToken"] else "***REDACTED***")
             for k, v in response.items()} if isinstance(response, dict) else type(response).__name__
        )

        tokens = _parse_cognito_response(response)

        if "RefreshToken" not in tokens and self._refresh_token:
            tokens["RefreshToken"] = self._refresh_token

        device_key = tokens.get("DeviceKey")
        device_group_key = tokens.get("DeviceGroupKey")
        access_token = tokens.get("AccessToken")

        if device_key and device_group_key and access_token:
            # Generate SRP verifier and confirm the device with Cognito
            _LOGGER.info(
                "NewDeviceMetadata received - confirming device %s... with Cognito",
                device_key[:20]
            )
            try:
                random_password, salt_b64, verifier_b64 = _generate_device_verifier(
                    device_group_key, device_key
                )

                def _confirm_device() -> Dict[str, Any]:
                    client = boto3.client("cognito-idp", region_name=self.region)
                    return client.confirm_device(
                        AccessToken=access_token,
                        DeviceKey=device_key,
                        DeviceSecretVerifierConfig={
                            'PasswordVerifier': verifier_b64,
                            'Salt': salt_b64,
                        },
                        DeviceName='Home Assistant',
                    )

                confirm_resp = await self.hass.async_add_executor_job(_confirm_device)
                user_confirmation_necessary = confirm_resp.get('UserConfirmationNecessary', False)
                _LOGGER.info(
                    "Device confirmed successfully: %s..., UserConfirmationNecessary: %s",
                    device_key[:20],
                    user_confirmation_necessary
                )

                # Mark device as "remembered" so Cognito trusts it for token refresh.
                # Without this step, GetTokensFromRefreshToken fails with
                # "Invalid Refresh Token" because the device is confirmed but not remembered.
                def _remember_device() -> None:
                    client = boto3.client("cognito-idp", region_name=self.region)
                    client.update_device_status(
                        AccessToken=access_token,
                        DeviceKey=device_key,
                        DeviceRememberedStatus='remembered',
                    )

                try:
                    await self.hass.async_add_executor_job(_remember_device)
                    _LOGGER.info(
                        "Device %s... marked as remembered",
                        device_key[:20]
                    )
                except ClientError as uerr:
                    error_code = uerr.response.get("Error", {}).get("Code", "Unknown")
                    error_msg = uerr.response.get("Error", {}).get("Message", str(uerr))
                    _LOGGER.warning(
                        "UpdateDeviceStatus failed (%s): %s. Token refresh may not work.",
                        error_code, error_msg
                    )
                except Exception as uerr:
                    _LOGGER.warning(
                        "UpdateDeviceStatus failed unexpectedly: %s", uerr
                    )

                # Store device password for potential future DEVICE_SRP_AUTH flows
                self._device_password = random_password

            except ClientError as err:
                error_code = err.response.get("Error", {}).get("Code", "Unknown")
                error_msg = err.response.get("Error", {}).get("Message", str(err))
                _LOGGER.warning(
                    "ConfirmDevice failed (%s): %s. Token refresh may not work with this device key.",
                    error_code, error_msg
                )
            except Exception as err:
                _LOGGER.warning(
                    "ConfirmDevice failed unexpectedly: %s. Token refresh may not work.",
                    err
                )
        elif not device_key:
            _LOGGER.warning(
                "No DeviceKey from Cognito SRP authentication response. "
                "Token refresh via GetTokensFromRefreshToken will not work. "
                "This may indicate device tracking is not enabled on the Cognito User Pool."
            )

        await self._apply_tokens(tokens)

    async def async_refresh(self) -> None:
        """Refresh tokens using Cognito GetTokensFromRefreshToken API.

        This uses the direct API call that mobile apps use for token refresh.
        It requires a DeviceKey from the initial authentication (obtained from Cognito's NewDeviceMetadata).

        Token rotation: Cognito may return a new RefreshToken, but often reuses
        the existing one. If no new RefreshToken is returned, the existing one is preserved.

        Uses a lock to prevent concurrent refresh attempts, which could cause
        "Invalid Refresh Token" errors if Cognito rotates the token.

        Reference: AWS Cognito GetTokensFromRefreshToken API
        Reference: https://docs.aws.amazon.com/cognito/latest/developerguide/amazon-cognito-user-pools-device-tracking.html
        """
        # Acquire lock to prevent concurrent refresh attempts
        async with self._refresh_lock:
            # Double-check conditions inside the lock in case they changed while waiting
            # Another coroutine may have already refreshed/re-logged while we waited
            if not self._is_token_expired():
                _LOGGER.info("Token was already refreshed by another coroutine; skipping")
                return

            if not self._refresh_token:
                _LOGGER.warning("Missing refresh token - performing full login")
                await self.async_full_login()
                return

            if not self._device_key:
                _LOGGER.error(
                    "Missing DeviceKey - cannot refresh tokens. Device was not confirmed during initial login. "
                    "Performing full login to re-authenticate and obtain device metadata."
                )
                await self.async_full_login()
                return

            # Validate refresh token format before attempting API call
            if not self._validate_refresh_token():
                _LOGGER.warning(
                    "Refresh token validation failed - token may be corrupted. "
                    "Attempting full login to obtain fresh tokens."
                )
                await self.async_full_login()
                return

            _LOGGER.debug(
                "Refreshing token using DeviceKey: %s..., RefreshToken length: %d",
                self._device_key[:10] if self._device_key else "None",
                len(self._refresh_token) if self._refresh_token else 0
            )

            def _sync() -> Dict[str, Any]:
                client = boto3.client("cognito-idp", region_name=self.region)

                return client._make_api_call(
                    "GetTokensFromRefreshToken",
                    {
                        "ClientId": self.client_id,
                        "RefreshToken": self._refresh_token,
                        "DeviceKey": self._device_key,
                        "ClientMetadata": {},
                    },
                )

            try:
                response = await self.hass.async_add_executor_job(_sync)

                tokens = _parse_cognito_response(response)

                # IMPORTANT: Cognito does NOT always return a new refresh token
                if "RefreshToken" not in tokens:
                    tokens["RefreshToken"] = self._refresh_token

                await self._apply_tokens(tokens)

                _LOGGER.info("Access token refreshed successfully via GetTokensFromRefreshToken")

            except ClientError as err:
                error_code = err.response.get("Error", {}).get("Code", "Unknown")
                error_msg = err.response.get("Error", {}).get("Message", str(err))

                if error_code == "NotAuthorizedException":
                    _LOGGER.warning(
                        "Cognito NotAuthorizedException during token refresh: %s. "
                        "This may indicate: invalid/expired refresh token, device key mismatch, "
                        "or user session revoked. RefreshToken first 20 chars: %s..., "
                        "DeviceKey first 10 chars: %s...",
                        error_msg,
                        self._refresh_token[:20] if self._refresh_token else "None",
                        self._device_key[:10] if self._device_key else "None"
                    )
                else:
                    _LOGGER.error(
                        "Cognito API error (%s) during token refresh: %s",
                        error_code,
                        error_msg
                    )

                _LOGGER.info("Falling back to full SRP login after token refresh failure")
                await self.async_full_login()

            except Exception as err:
                _LOGGER.error(
                    "Unexpected error during token refresh (type: %s): %s. Falling back to full SRP login.",
                    type(err).__name__,
                    err
                )
                await self.async_full_login()

    async def ensure_valid_token(self) -> None:
        """Ensure we have a valid access token, refreshing or re-authenticating if needed."""
        if not self._access_token:
            await self.async_full_login()
            return
        # If we don't have a refresh token, force a full login to ensure stability
        if not self._refresh_token:
            _LOGGER.info("No refresh token stored: forcing full SRP login")
            await self.async_full_login()
            return

        if self._is_token_expired():
            _LOGGER.info("Access token expiring, refreshing via GetTokensFromRefreshToken")
            try:
                await self.async_refresh()
            except Exception as err:
                _LOGGER.warning("Token refresh failed, falling back to full SRP login: %s", err)
                await self.async_full_login()

    async def get_access_token(self) -> str:
        await self.ensure_valid_token()
        assert self._access_token is not None
        return self._access_token

    async def get_id_token(self) -> str:
        await self.ensure_valid_token()
        assert self._id_token is not None
        return self._id_token

    async def get_user_id(self) -> str:
        """Fetch the Cognito user identifier (sub)."""

        def _sync(access_token: str) -> Optional[str]:
            client = boto3.client("cognito-idp", region_name=self.region)
            resp = client.get_user(AccessToken=access_token)
            attrs = {a["Name"]: a["Value"] for a in resp.get("UserAttributes", [])}
            return attrs.get("sub")

        access_token = await self.get_access_token()
        user_id = await self.hass.async_add_executor_job(_sync, access_token)

        if not user_id:
            raise RuntimeError("Failed to get user_id")

        return user_id

    async def get_aws_credentials(self) -> Dict[str, Any]:
        """Exchange IdToken for AWS credentials from the identity pool."""
        await self.ensure_valid_token()

        # Reuse cached credentials until they are about to expire
        if self._cached_credentials:
            expiration = self._cached_credentials.get("expiration")
            if isinstance(expiration, datetime):
                if expiration.timestamp() - time.time() > 300:
                    return self._cached_credentials

        def _sync(id_token: str) -> Dict[str, Any]:
            identity = boto3.client("cognito-identity", region_name=self.region)
            provider = COGNITO_ISSUER.format(region=self.region, user_pool_id=self.user_pool_id)

            identity_id = identity.get_id(
                IdentityPoolId=self.identity_pool_id,
                Logins={provider: id_token},
            )["IdentityId"]

            creds = identity.get_credentials_for_identity(
                IdentityId=identity_id,
                Logins={provider: id_token},
            )["Credentials"]

            return creds

        id_token = await self.get_id_token()
        creds = await self.hass.async_add_executor_job(_sync, id_token)

        self._cached_credentials = {
            "access_key": creds["AccessKeyId"],
            "secret_key": creds["SecretKey"],
            "session_token": creds["SessionToken"],
            "expiration": creds["Expiration"],
        }

        return self._cached_credentials
