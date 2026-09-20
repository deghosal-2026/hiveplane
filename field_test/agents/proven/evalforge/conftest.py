"""Shared fixtures and markers for field tests."""
import pytest


@pytest.fixture(scope="session")
def field_results_dir():
    """Root directory for field test results."""
    import os
    return os.environ.get("EVALFORGE_FIELD_RESULTS_DIR", "field/results")


def pytest_configure(config):
    config.addinivalue_line("markers", "field: mark test as a field test")
    config.addinivalue_line("markers", "field_agent(agent_id): identify which agent a test runs against")
    config.addinivalue_line("markers", "field_category(category): langgraph | pydantic-ai | http | subprocess")
    config.addinivalue_line("markers", "field_expensive: mark tests that use paid LLM APIs")
