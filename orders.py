from kiteconnect import KiteConnect
import logging
import config
import pandas as pd
import os, sys
import datetime
import logging
import time
from utils import zerodha_login
# Import ProStocks API related modules
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("stock_scanner.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)
kite = zerodha_login()

def get_top_3_futures_from_tv_symbol(tv_symbol, exchange="NFO"):
    """
    Handles TradingView symbol like 'RELIANCE!' and returns top 3 NSE futures contracts.

    Args:
        tv_symbol (str): TradingView symbol, e.g., 'RELIANCE!' or 'RELIANCE'
        exchange (str): Exchange, default is 'NSE'

    Returns:
        list of dict: List of futures contracts with tradingsymbol, expiry, lot_size
    """
    # Clean TradingView symbol to extract actual stock name
    if tv_symbol.endswith("!"):
        symbol = tv_symbol[:-1]
    else:
        symbol = tv_symbol

    try:
        instruments = kite.instruments(exchange=exchange)
        df = pd.DataFrame(instruments)

        # Filter for stock futures (FUTSTK) of the given symbol
        fut_df = df[
            (df['instrument_type'] == 'FUT') &
            (df['name'] == symbol.upper())
        ]

        if fut_df.empty:
            print(f"No futures contracts found for {symbol} on {exchange}")
            return []

        # Sort by expiry and pick top 3
        fut_df_sorted = fut_df.sort_values('expiry').head(3)

        contracts = []
        for _, row in fut_df_sorted.iterrows():
            contracts.append({
                "tradingsymbol": row["tradingsymbol"],
                "expiry": row["expiry"],
                "lot_size": row["lot_size"]
            })

        return contracts

    except Exception as e:
        print(f"Error fetching futures contracts: {e}")
        return []

# Place Order Function
def place_order(symbol, action, price, segment, quantity=1):
    """
    Place a market order via Zerodha Kite API.
    action: "buy" or "sell"
    symbol: TradingView ticker (NSE:RELIANCE), extract actual symbol
    price: Price from alert (optional, market order used)
    quantity: Number of shares/lots
    """
    try:
        exchange = segment
        tradingsymbol = symbol

        order_params = {
            "tradingsymbol": tradingsymbol,
            "exchange": exchange,
            "transaction_type": KiteConnect.TRANSACTION_TYPE_BUY if action == "buy" else KiteConnect.TRANSACTION_TYPE_SELL,
            "quantity": quantity,
            "order_type": KiteConnect.ORDER_TYPE_MARKET,
            "product": KiteConnect.PRODUCT_NRML,  # Or CNC for delivery
            "variety": KiteConnect.VARIETY_REGULAR
        }

        order_id = kite.place_order(**order_params)
        logging.info(f"{action.upper()} order placed for {tradingsymbol}. Order ID: {order_id}")
        return order_id

    except Exception as e:
        logging.error(f"Error placing order: {e}")
