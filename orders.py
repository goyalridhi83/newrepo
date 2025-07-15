import os
import logging
import pandas as pd
from kiteconnect import KiteConnect
from dotenv import load_dotenv
from typing import List, Dict, Any, Optional

# Load environment variables
load_dotenv()
API_KEY = os.getenv("KITE_API_KEY")
API_SECRET = os.getenv("KITE_API_SECRET")

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

def get_top_3_futures_from_tv_symbol(tv_symbol: str, kite: KiteConnect, exchange: str = "NFO") -> List[Dict[str, Any]]:
    """
    Retrieve the top 3 nearest expiry futures contracts for a given symbol from TradingView notation.
    Args:
        tv_symbol (str): TradingView symbol, e.g. 'RELIANCE!'
        kite (KiteConnect): KiteConnect API instance
        exchange (str): Exchange, default 'NFO'
    Returns:
        List of dicts with 'tradingsymbol', 'expiry', 'lot_size'.
    Raises:
        Exception: If fetching instruments fails.
    """
    symbol = tv_symbol[:-1] if tv_symbol.endswith("!") else tv_symbol
    if( symbol.endswith("1") or symbol.endswith("2") or symbol.endswith("3")):
        symbol = symbol[:-1]
    try:
        instruments = kite.instruments(exchange=exchange)
        df = pd.DataFrame(instruments)
        fut_df = df[(df['instrument_type'] == 'FUT') & (df['name'] == symbol.upper())]
        if fut_df.empty:
            logger.warning(f"No futures contracts found for {symbol} on {exchange}")
            return []
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
        logger.error(f"Error fetching futures contracts: {e}")
        return []

def place_order(
    kite: KiteConnect,
    tradingsymbol: str,
    action: str,
    price: float,
    segment: str,
    quantity: int = 1
) -> Optional[str]:
    """
    Place an order via the KiteConnect API.
    Args:
        kite (KiteConnect): KiteConnect API instance
        tradingsymbol (str): Trading symbol
        action (str): 'buy' or 'sell'
        price (float): Price (currently unused, as order_type is MARKET)
        segment (str): Exchange segment
        quantity (int): Quantity to trade
    Returns:
        Order ID if successful, None otherwise.
    Raises:
        Exception: If placing order fails.
    """
    try:
        exchange = segment
        # Set product type based on segment
        if segment == "NFO":
            product_type = KiteConnect.PRODUCT_NRML
        elif segment == "NSE":
            product_type = KiteConnect.PRODUCT_CNC
        else:
            product_type = KiteConnect.PRODUCT_NRML  # Default fallback

        order_params = {
            "tradingsymbol": tradingsymbol,
            "exchange": exchange,
            "transaction_type": KiteConnect.TRANSACTION_TYPE_BUY if action == "buy" else KiteConnect.TRANSACTION_TYPE_SELL,
            "quantity": quantity,
            "order_type": KiteConnect.ORDER_TYPE_MARKET,
            "product": product_type,
            "variety": KiteConnect.VARIETY_REGULAR
        }
        order_id = kite.place_order(**order_params)
        logger.info(f"{action.upper()} order placed for {tradingsymbol}. Order ID: {order_id}")
        return (order_id, None)
    except Exception as e:
        logger.error(f"Error placing order: {e}")
        return (None, str(e))
