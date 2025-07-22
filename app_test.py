import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch, MagicMock
from app import app, WEBHOOK_SECRET, API_SECRET

# Create a test client for the FastAPI app
client = TestClient(app)

@pytest.fixture(autouse=True)
def mock_dependencies():
    """Mock external dependencies for all tests in this file."""
    with patch('app.zerodha_login', return_value=MagicMock()) as mock_login, \
         patch('app.is_duplicate') as mock_is_duplicate, \
         patch('app.place_order') as mock_place_order, \
         patch('app.get_top_3_futures_from_tv_symbol') as mock_get_contracts:
        
        # Make mocks available to tests if needed
        yield {
            "login": mock_login,
            "is_duplicate": mock_is_duplicate,
            "place_order": mock_place_order,
            "get_contracts": mock_get_contracts
        }

def test_webhook_buy_order_success(mock_dependencies):
    """Test a successful buy order via the webhook."""
    mock_dependencies['is_duplicate'].return_value = False
    mock_dependencies['place_order'].return_value = ("12345", None)
    
    response = client.post(
        f"/webhook?token={WEBHOOK_SECRET}",
        json={"action": "buy", "symbol": "RELIANCE", "segment": "NSE", "time": "2024-01-01T12:00:00Z"}
    )
    
    assert response.status_code == 200
    assert response.json()["status"] == "buy order placed"

def test_webhook_sell_order_failure(mock_dependencies):
    """Test a failed sell order when the user has no holdings."""
    mock_dependencies['is_duplicate'].return_value = False
    
    # Simulate having no holdings by mocking the positions call inside the endpoint
    with patch('app.kite.positions', return_value={"net": []}), \
         patch('app.kite.holdings', return_value=[]):
        response = client.post(
            f"/webhook?token={WEBHOOK_SECRET}",
            json={"action": "sell", "symbol": "RELIANCE", "segment": "NSE", "time": "2024-01-01T12:00:00Z"}
        )
    
    assert response.status_code == 200
    assert response.json()["status"] == "no holdings, sell skipped"

def test_webhook_unauthorized():
    """Test webhook access with an invalid token."""
    response = client.post(
        "/webhook?token=invalid_token",
        json={"action": "buy", "symbol": "RELIANCE", "time": "2024-01-01T12:00:00Z"}
    )
    assert response.status_code == 401

def test_token_save_and_refresh_success():
    """Test successful token generation and validation."""
    with patch('app.kite.generate_session') as mock_gen_session, \
         patch('app.kite.profile') as mock_profile, \
         patch('builtins.open', new_callable=MagicMock):
        
        mock_gen_session.return_value = {"access_token": "new_token"}
        mock_profile.return_value = {"user_id": "AB1234"}
        
        response = client.post("/token", data={"token": "test_request_token"})
        
        assert response.status_code == 200
        assert "Token is valid" in response.text

def test_token_save_and_refresh_failure():
    """Test when token generation fails."""
    with patch('app.kite.generate_session', side_effect=Exception("Invalid token")):
        response = client.post("/token", data={"token": "invalid_request_token"})
        assert response.status_code == 500
        assert "An internal error occurred" in response.text 