import pytest
import pandas as pd
from unittest.mock import patch, MagicMock
import redis_utils


def test_is_duplicate_sets_and_detects_duplicate(monkeypatch):
    mock_redis = MagicMock()
    # First call: key does not exist, so set returns True
    mock_redis.set.return_value = True
    monkeypatch.setattr(redis_utils, "redis_client", mock_redis)
    assert redis_utils.is_duplicate("SYM", "123") is False
    # Second call: key exists, so set returns False
    mock_redis.set.return_value = False
    assert redis_utils.is_duplicate("SYM", "123") is True

def test_set_and_get_instrument_cache(monkeypatch):
    mock_redis = MagicMock()
    monkeypatch.setattr(redis_utils, "redis_client", mock_redis)
    df = pd.DataFrame({"instrument_type": ["FUT"], "name": ["NIFTY"], "tradingsymbol": ["NIFTY24JULFUT"], "expiry": ["2024-07-25"], "lot_size": [50]})
    # Test set_instrument_cache
    redis_utils.set_instrument_cache("NFO", df)
    assert mock_redis.set.called
    # Test get_instrument_cache returns DataFrame
    json_data = df.to_json(orient="split")
    mock_redis.get.return_value = json_data
    result = redis_utils.get_instrument_cache("NFO")
    assert isinstance(result, pd.DataFrame)
    assert result.iloc[0]["name"] == "NIFTY"
    # Test get_instrument_cache returns None if not found
    mock_redis.get.return_value = None
    assert redis_utils.get_instrument_cache("NFO") is None

def test_check_redis_connection_success(monkeypatch):
    mock_redis = MagicMock()
    mock_redis.ping.return_value = True
    monkeypatch.setattr(redis_utils, "redis_client", mock_redis)
    assert redis_utils.check_redis_connection() is True

def test_check_redis_connection_failure(monkeypatch):
    mock_redis = MagicMock()
    mock_redis.ping.side_effect = Exception("fail")
    monkeypatch.setattr(redis_utils, "redis_client", mock_redis)
    assert redis_utils.check_redis_connection() is False 