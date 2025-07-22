import pytest
from unittest.mock import patch, MagicMock
from utils import zerodha_login

@patch('utils.os.path.exists')
@patch('utils.open', new_callable=MagicMock)
@patch('utils.KiteConnect')
def test_zerodha_login_with_access_token(mock_kite_connect, mock_open, mock_exists):
    """Test login succeeds when access_token.txt exists."""
    mock_exists.return_value = True
    mock_open.return_value.read.return_value = "test_access_token"
    mock_kite_instance = MagicMock()
    mock_kite_connect.return_value = mock_kite_instance
    
    kite = zerodha_login()
    
    mock_kite_instance.set_access_token.assert_called_once_with("test_access_token")
    assert kite is not None

@patch('utils.os.path.exists')
@patch('utils.KiteConnect')
def test_zerodha_login_without_access_token(mock_kite_connect, mock_exists):
    """Test login proceeds without access token if file is missing."""
    mock_exists.return_value = False
    mock_kite_instance = MagicMock()
    mock_kite_connect.return_value = mock_kite_instance
    
    zerodha_login()
    
    mock_kite_instance.set_access_token.assert_not_called() 