"""Behavioral checks for coordinator port caching after transport disconnections.

Load the unmodified coordinator methods from their AST to avoid requiring
an entire Home Assistant runtime for this narrowly scoped unit suite.
"""

import ast
import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, Mock

import pytest

from test_connection_recovery import core


COORDINATOR = (
    Path(__file__).resolve().parents[1]
    / "custom_components/tplink_easy_smart/update_coordinator.py"
)


class UpdateFailed(Exception):
    """Isolated stand-in for HA's coordinator exception."""


def load_coordinator_methods():
    tree = ast.parse(COORDINATOR.read_text(encoding="utf-8"))
    cls = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef)
        and node.name == "TpLinkDataUpdateCoordinator"
    )
    wanted = {
        "async_update",
        "_update_port_states",
        "_update_poe_state",
        "_update_port_poe_states",
    }
    cls.body = [
        node
        for node in cls.body
        if isinstance(node, ast.AsyncFunctionDef) and node.name in wanted
    ]
    assert {node.name for node in cls.body} == wanted
    cls.bases = []
    cls.keywords = []
    cls.decorator_list = []
    module = ast.fix_missing_locations(ast.Module(body=[cls], type_ignores=[]))
    namespace = {
        "_LOGGER": Mock(),
        "ApiCallError": core.ApiCallError,
        "APICALL_ERRCAT_DISCONNECTED": core.APICALL_ERRCAT_DISCONNECTED,
        "UpdateFailed": UpdateFailed,
        "FEATURE_POE": "feature_poe",
    }
    exec(compile(module, str(COORDINATOR), "exec"), namespace)
    return namespace["TpLinkDataUpdateCoordinator"]


def coordinator():
    c = load_coordinator_methods()()
    c._api = Mock()
    c._port_states = ["cached-port"]
    c._port_poe_states = ["cached-poe"]
    c._poe_state = "cached-poe-status"
    c._update_switch_info = AsyncMock()
    c.is_feature_available = AsyncMock(return_value=True)
    return c


def disconnect():
    return core.ApiCallError(
        "Switch dropped HTTP connection",
        core.APICALL_ERRCODE_DISCONNECTED,
        core.APICALL_ERRCAT_DISCONNECTED,
    )


def test_poll_failure_preserves_last_known_ports_and_marks_update_failed():
    c = coordinator()
    c._api.get_port_states = AsyncMock(side_effect=disconnect())
    c._update_poe_state = AsyncMock()
    c._update_port_poe_states = AsyncMock()
    with pytest.raises(UpdateFailed, match="Switch API connection unavailable"):
        asyncio.run(c.async_update())
    assert c._port_states == ["cached-port"]
    c._update_poe_state.assert_not_awaited()


def test_successful_port_poll_replaces_cached_state():
    c = coordinator()
    c._api.get_port_states = AsyncMock(return_value=["new-port"])
    c._update_poe_state = AsyncMock()
    c._update_port_poe_states = AsyncMock()
    asyncio.run(c.async_update())
    assert c._port_states == ["new-port"]


def test_poe_port_disconnection_preserves_existing_cache():
    c = coordinator()
    c._api.get_port_poe_states = AsyncMock(side_effect=disconnect())
    with pytest.raises(core.ApiCallError):
        asyncio.run(c._update_port_poe_states())
    assert c._port_poe_states == ["cached-poe"]


def test_poe_status_disconnection_preserves_existing_cache():
    c = coordinator()
    c._api.get_poe_state = AsyncMock(side_effect=disconnect())
    with pytest.raises(core.ApiCallError):
        asyncio.run(c._update_poe_state())
    assert c._poe_state == "cached-poe-status"
