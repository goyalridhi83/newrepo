import pytest
import pandas as pd
from unittest.mock import MagicMock, patch
from orders import get_top_3_futures_from_tv_symbol, place_order

@pytest.fixture
def mock_kite():
    """Fixture for a mocked KiteConnect instance."""
    return MagicMock()

@patch('orders.get_instrument_cache')
@patch('orders.set_instrument_cache')
def test_get_top_3_futures_cache_miss(mock_set_cache, mock_get_cache, mock_kite):
    """Test contract fetching when instruments are not in cache."""
    mock_get_cache.return_value = None
    instrument_data = [
        {'instrument_type': 'FUT', 'name': 'NIFTY', 'tradingsymbol': 'NIFTY24JULFUT', 'expiry': '2024-07-25', 'lot_size': 50},
        {'instrument_type': 'FUT', 'name': 'NIFTY', 'tradingsymbol': 'NIFTY24AUGFUT', 'expiry': '2024-08-29', 'lot_size': 50},
    ]
    mock_kite.instruments.return_value = instrument_data
    
    contracts = get_top_3_futures_from_tv_symbol("NIFTY!", mock_kite, "NFO")
    
    mock_get_cache.assert_called_once_with("NFO")
    mock_kite.instruments.assert_called_once_with(exchange="NFO")
    mock_set_cache.assert_called_once()
    assert len(contracts) == 2
    assert contracts[0]['tradingsymbol'] == 'NIFTY24JULFUT'

@patch('orders.get_instrument_cache')
def test_get_top_3_futures_cache_hit(mock_get_cache, mock_kite):
    """Test contract fetching when instruments are already in cache."""
    df = pd.DataFrame([
        {'instrument_type': 'FUT', 'name': 'NIFTY', 'tradingsymbol': 'NIFTY24JULFUT', 'expiry': '2024-07-25', 'lot_size': 50},
        {'instrument_type': 'FUT', 'name': 'NIFTY', 'tradingsymbol': 'NIFTY24AUGFUT', 'expiry': '2024-08-29', 'lot_size': 50},
    ])
    mock_get_cache.return_value = df
    
    contracts = get_top_3_futures_from_tv_symbol("NIFTY!", mock_kite, "NFO")
    
    mock_get_cache.assert_called_once_with("NFO")
    mock_kite.instruments.assert_not_called()
    assert len(contracts) == 2

def test_place_order_success(mock_kite):
    """Test successful order placement."""
    mock_kite.place_order.return_value = "12345"
    order_id, error = place_order(mock_kite, "NIFTY50", "buy", 0, "NSE", 10)
    
    mock_kite.place_order.assert_called_once()
    assert order_id == "12345"
    assert error is None

def test_place_order_failure(mock_kite):
    """Test failed order placement."""
    mock_kite.place_order.side_effect = Exception("Order failed")
    order_id, error = place_order(mock_kite, "NIFTY50", "sell", 0, "NSE", 10)
    
    assert order_id is None
    assert "Order failed" in error 