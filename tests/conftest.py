import os
import pytest


@pytest.fixture(autouse=True, scope="session")
def setup_lemming_home(tmp_path_factory):
    """Set up a temporary LEMMING_HOME for the entire test session."""
    tmp_home = tmp_path_factory.mktemp("lemming_home")
    os.environ["LEMMING_HOME"] = str(tmp_home)
    return tmp_home
