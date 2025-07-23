import logging
from kiteconnect import KiteConnect
from typing import List, Dict, Any, Tuple, Optional
import pandas as pd

from redis_utils import get_instrument_cache, set_instrument_cache
from memory_manager import cleanup_dataframes

logger = logging.getLogger(__name__)


def get_top_3_futures_from_tv_symbol(tv_symbol: str, kite: KiteConnect, exchange: str = "NFO") -> List[Dict[str, Any]]:
    """
    Retrieve the top 3 nearest expiry futures contracts for a given symbol.
    Uses a Redis cache for the instrument list.
    """
    symbol = tv_symbol[:-1] if tv_symbol.endswith("!") else tv_symbol
    if symbol.endswith(("1", "2", "3")):
        symbol = symbol[:-1]
    
    try:
        df = get_instrument_cache(exchange)

        if df is None:
            logger.info(f"Instrument cache miss for {exchange}. Fetching from API.")
            instruments = kite.instruments(exchange=exchange)
            df = pd.DataFrame(instruments)
            set_instrument_cache(exchange, df)
            # Note: df will be cleaned up in the finally block below

        fut_df = df[(df['instrument_type'] == 'FUT') & (df['name'] == symbol.upper())]
        
        if fut_df.empty:
            logger.warning(f"No futures contracts found for {symbol} on {exchange}")
            cleanup_dataframes(fut_df)
            return []

        fut_df_sorted = fut_df.sort_values('expiry').head(3)
        
        contracts = []
        try:
            for _, row in fut_df_sorted.iterrows():
                contracts.append({
                    "tradingsymbol": row["tradingsymbol"],
                    "expiry": row["expiry"],
                    "lot_size": row["lot_size"]
                })
            return contracts
        finally:
            # Clean up DataFrames to prevent memory leaks
            cleanup_dataframes(fut_df, fut_df_sorted)
    except Exception as e:
        logger.error(f"Error fetching futures contracts: {e}", exc_info=True)
        return []
    finally:
        # Clean up the main DataFrame if it exists
        if 'df' in locals() and df is not None:
            cleanup_dataframes(df)


def place_order(
    kite: KiteConnect,
    tradingsymbol: str,
    action: str,
    price: float,
    segment: str,
    quantity: int = 1
) -> Tuple[Optional[str], Optional[str]]:
    """
    Place a MARKET order via the KiteConnect API.

    Args:
        kite (KiteConnect): Authenticated KiteConnect instance.
        tradingsymbol (str): The trading symbol for the order.
        action (str): 'buy' or 'sell'.
        price (float): The price for the order (unused for market orders).
        segment (str): The exchange segment (e.g., 'NSE', 'NFO').
        quantity (int): The quantity to trade.

    Returns:
        A tuple containing the order ID and None on success,
        or (None, error_message) on failure.
    """
    try:
        exchange = segment
        if segment == "NFO":
            product_type = kite.PRODUCT_NRML
        elif segment == "NSE":
            product_type = kite.PRODUCT_CNC
        else:
            product_type = kite.PRODUCT_NRML

        order_params = {
            "tradingsymbol": tradingsymbol,
            "exchange": exchange,
            "transaction_type": kite.TRANSACTION_TYPE_BUY if action == "buy" else kite.TRANSACTION_TYPE_SELL,
            "quantity": quantity,
            "order_type": kite.ORDER_TYPE_MARKET,
            "product": product_type,
            "variety": kite.VARIETY_REGULAR
        }
        order_id = kite.place_order(**order_params)
        logger.info(f"✅ {action.upper()} order placed for {tradingsymbol}. Order ID: {order_id}")
        return (order_id, None)
    except Exception as e:
        logger.error(f"❌ Error placing order for {tradingsymbol}: {e}", exc_info=True)
        return (None, str(e))
