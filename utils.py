import os
from kiteconnect import KiteConnect


def zerodha_login(api_key=None, api_secret=None, request_token=None, access_token_path="token/access_token.txt"):
    """
    Logs into Zerodha using Kite Connect API and sets the access token.
    If access token exists in file, uses it; else, performs login flow.

    Args:
        api_key (str): Zerodha API key. If None, reads from env KITE_API_KEY.
        api_secret (str): Zerodha API secret. If None, reads from env KITE_API_SECRET.
        request_token (str): Request token obtained after browser login. If None, prompts user.
        access_token_path (str): Path to store/retrieve access token.

    Returns:
        KiteConnect: Authenticated KiteConnect instance.
    """
    api_key = "wt1b63ihts1q60wt"
    api_secret = "rhl5o9yydobp4hfpmgl3lx9kkotnyk0t"

    api_key = api_key or os.getenv("KITE_API_KEY")
    api_secret = api_secret or os.getenv("KITE_API_SECRET")
    if not api_key or not api_secret:
        raise Exception("API key/secret not provided. Set env variables or pass as arguments.")

    kite = KiteConnect(api_key=api_key)

    # Try to load access token from file
    if os.path.exists(access_token_path):
        with open(access_token_path, "r") as f:
            access_token = f.read().strip()
        kite.set_access_token(access_token)
        # Optionally, verify token by making a test API call
        try:
            profile = kite.profile()
            print("Access token loaded and verified.")
            return kite
        except Exception as e:
            print(f"Stored access token invalid: {e}. Re-authenticating...")

    # If no valid access token, perform login flow
    if not request_token:
        print("\n1. Visit this URL and login to your Zerodha account:")
        print(kite.login_url())
        request_token = input("2. After login, paste the 'request_token' from the URL here: ").strip()
    data = kite.generate_session(request_token, api_secret=api_secret)
    access_token = data["access_token"]
    # Save access token for future use
    with open(access_token_path, "w") as f:
        f.write(access_token)
    kite.set_access_token(access_token)
    print("Access token generated and saved.")
    return kite

# Example usage (uncomment to test standalone):
# kite = zerodha_login()
# print(kite.profile())

def fetch_latest_data(kite, instrument_token, interval='15minute', lookback=15*4):
    """Fetch latest market data for backtesting or signal generation."""
    to_date = datetime.now()
    from_date = to_date - timedelta(minutes=lookback*15)
    data = kite.historical_data(
        instrument_token=instrument_token,
        from_date=from_date,
        to_date=to_date,
        interval=interval,
        continuous=False,
        oi=False
    )
    df = pd.DataFrame(data)
    if not df.empty:
        df['date'] = pd.to_datetime(df['date'])
        df.set_index('date', inplace=True)
    return df

def get_instrument_token(kite, tradingsymbol):
    """Get instrument token for a given tradingsymbol."""
    instruments = kite.instruments("NSE")
    for inst in instruments:
        if inst['tradingsymbol'] == tradingsymbol:
            return inst['instrument_token']
    return None

zerodha_login()
