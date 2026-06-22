import pytest
from oracle import config


@pytest.fixture
def c():
    return config.load()
