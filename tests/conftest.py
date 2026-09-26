"""Fixtures for the tests that run a real Home Assistant (test_integration.py).

test_report.py and test_config_flow.py are plain scripts and need none of this.
"""

import pytest


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(recorder_mock, enable_custom_integrations):
    """A recorder (the integration depends on it; it must start before hass),
    and permission to load custom_components/energy_report."""
    yield
