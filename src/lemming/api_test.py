from lemming import api


def test_api_proxy():
    # Verify that the proxy re-exports work
    assert hasattr(api, "app")
    assert hasattr(api, "QuietPollFilter")
