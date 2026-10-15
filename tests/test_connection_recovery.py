"""Tests for dropped TP-Link Easy Smart HTTP sessions (no HA runtime needed)."""

import asyncio
import importlib.util
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from aiohttp import ServerDisconnectedError


SOURCE = Path(__file__).resolve().parents[1] / "custom_components/tplink_easy_smart/client/coreapi.py"
spec = importlib.util.spec_from_file_location("tplink_coreapi_transport_test", SOURCE)
core = importlib.util.module_from_spec(spec)
spec.loader.exec_module(core)


def new_api():
    api = core.TpLinkWebApi("127.0.0.1", 80, False, "admin", "password", False)
    api._is_initialized = True  # fake unit-test state, no network used
    return api


def response(text="OK", exception=None):
    reader = (
        AsyncMock(side_effect=exception)
        if exception is not None
        else AsyncMock(return_value=text.encode())
    )
    return SimpleNamespace(status=200, content=SimpleNamespace(read=reader))


def disconnected():
    return core.ApiCallError(
        "device disconnected",
        core.APICALL_ERRCODE_DISCONNECTED,
        core.APICALL_ERRCAT_DISCONNECTED,
    )


def test_read_only_get_retries_single_disconnect(monkeypatch):
    api = new_api()
    api._get_raw = AsyncMock(side_effect=[disconnected(), response("device data")])
    monkeypatch.setattr(core.asyncio, "sleep", AsyncMock())
    assert asyncio.run(api.get("PortSettingRpm.htm")) == "device data"
    assert api._get_raw.await_count == 2


def test_disconnect_during_response_read_is_retried(monkeypatch):
    api = new_api()
    api._get_raw = AsyncMock(
        side_effect=[
            response(exception=ServerDisconnectedError()),
            response("ok"),
        ]
    )
    monkeypatch.setattr(core.asyncio, "sleep", AsyncMock())
    assert asyncio.run(api.get("PortSettingRpm.htm")) == "ok"
    assert api._get_raw.await_count == 2


def test_read_only_get_stops_after_one_retry(monkeypatch):
    api = new_api()
    api._get_raw = AsyncMock(side_effect=[disconnected(), disconnected()])
    monkeypatch.setattr(core.asyncio, "sleep", AsyncMock())
    with pytest.raises(core.ApiCallError) as exc:
        asyncio.run(api.get("PortSettingRpm.htm"))
    assert exc.value.category == core.APICALL_ERRCAT_DISCONNECTED
    assert api._get_raw.await_count == 2


def test_disconnect_during_second_body_read_is_reported(monkeypatch):
    api = new_api()
    api._get_raw = AsyncMock(
        side_effect=[
            response(exception=ServerDisconnectedError()),
            response(exception=ServerDisconnectedError()),
        ]
    )
    monkeypatch.setattr(core.asyncio, "sleep", AsyncMock())
    with pytest.raises(core.ApiCallError) as exc:
        asyncio.run(api.get("PortSettingRpm.htm"))
    assert exc.value.code == core.APICALL_ERRCODE_DISCONNECTED
    assert api._get_raw.await_count == 2


def test_non_disconnect_error_is_not_retried(monkeypatch):
    api = new_api()
    api._get_raw = AsyncMock(
        side_effect=core.ApiCallError("bad reply", -3, "request_error")
    )
    monkeypatch.setattr(core.asyncio, "sleep", AsyncMock())
    with pytest.raises(core.ApiCallError):
        asyncio.run(api.get("PortSettingRpm.htm"))
    assert api._get_raw.await_count == 1


def test_post_write_is_not_retried_automatically():
    api = new_api()
    api._post_raw = AsyncMock(side_effect=disconnected())
    with pytest.raises(core.ApiCallError):
        asyncio.run(api.post("port_setting.cgi", {"portid": "1"}))
    assert api._post_raw.await_count == 1


def test_login_transport_disconnection_is_not_bad_credentials():
    api = new_api()
    api._is_initialized = False
    api._refresh_session = Mock()
    api._post_raw = AsyncMock(side_effect=disconnected())
    with pytest.raises(core.ApiCallError) as exc:
        asyncio.run(api._ensure_initialized())
    assert exc.value.code == core.APICALL_ERRCODE_DISCONNECTED
    assert api._is_initialized is False
    assert api._post_raw.await_count == 1


def test_login_body_disconnect_is_transport_error():
    api = new_api()
    api._refresh_session = Mock()
    api._post_raw = AsyncMock(
        return_value=response(exception=ServerDisconnectedError())
    )
    with pytest.raises(core.ApiCallError) as exc:
        asyncio.run(api.authenticate())
    assert exc.value.category == core.APICALL_ERRCAT_DISCONNECTED
    assert api._post_raw.await_count == 1


def test_real_credential_rejection_still_authentication_error():
    api = new_api()
    api._refresh_session = Mock()
    api._post_raw = AsyncMock(
        return_value=response('<script>var logonInfo = new Array(1,0,0);</script>')
    )
    with pytest.raises(core.AuthenticationError) as exc:
        asyncio.run(api.authenticate())
    assert exc.value.reason_code == core.AUTH_FAILURE_CREDENTIALS
