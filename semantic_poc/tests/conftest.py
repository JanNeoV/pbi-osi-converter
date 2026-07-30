from __future__ import annotations

import pytest


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--run-live-agent",
        action="store_true",
        default=False,
        help="Run explicitly opted-in, cost-bearing OpenAI API tests.",
    )


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if config.getoption("--run-live-agent"):
        return
    marker = pytest.mark.skip(reason="live agent tests require --run-live-agent")
    for item in items:
        if "live_agent" in item.keywords:
            item.add_marker(marker)
