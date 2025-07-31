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
    
    df = None
    fut_df = None
    fut_df_sorted = None
    
    try:
        df = get_instrument_cache(exchange)

        if df is None:
            logger.info(f"Instrument cache miss for {exchange}. Fetching from API.")
            instruments = kite.instruments(exchange=exchange)
            df = pd.DataFrame(instruments)
            set_instrument_cache(exchange, df)

        # Create filtered DataFrame with explicit copy to avoid view warnings
        fut_df = df[(df['instrument_type'] == 'FUT') & (df['name'] == symbol.upper())].copy()
        
        if fut_df.empty:
            logger.warning(f"No futures contracts found for {symbol} on {exchange}")
            return []

        # Sort and get top 3 contracts
        fut_df_sorted = fut_df.sort_values('expiry').head(3).copy()
        
        contracts = []
        for _, row in fut_df_sorted.iterrows():
            contracts.append({
                "tradingsymbol": row["tradingsymbol"],
                "expiry": row["expiry"],
                "lot_size": row["lot_size"]
            })
        
        logger.debug(f"Found {len(contracts)} futures contracts for {symbol} on {exchange}")
        return contracts
        
    except Exception as e:
        logger.error(f"Error fetching futures contracts: {e}", exc_info=True)
        return []
    finally:
        # Always cleanup all DataFrames to prevent memory leaks
        dataframes_to_cleanup = []
        
        if fut_df is not None:
            dataframes_to_cleanup.append(fut_df)
        if fut_df_sorted is not None:
            dataframes_to_cleanup.append(fut_df_sorted)
        if df is not None:
            dataframes_to_cleanup.append(df)
            
        if dataframes_to_cleanup:
            cleanup_dataframes(*dataframes_to_cleanup)
            
        # Explicit cleanup of variables
        fut_df = None
        fut_df_sorted = None
        df = None

def check_existing_pending_orders(
    kite: KiteConnect,
    tradingsymbol: str,
    action: str,
    segment: str,
    quantity: int
) -> Tuple[bool, Optional[str]]:
    """
    Check if there are existing pending orders for the same symbol and action.
    
    Args:
        kite: Authenticated KiteConnect instance
        tradingsymbol: The trading symbol to check
        action: 'buy' or 'sell'
        segment: Exchange segment
        quantity: Order quantity
        
    Returns:
        Tuple of (has_duplicate, order_details)
        - has_duplicate: True if duplicate pending order exists
        - order_details: Details of the existing order if found
    """
    try:
        # Get all pending orders
        orders = kite.orders()
        
        # Define pending statuses (orders that are not yet executed or cancelled)
        pending_statuses = [
            "OPEN",           # Order is open/pending
            "TRIGGER PENDING", # Stop-loss order waiting for trigger
            "PENDING",        # General pending status
            "AMO REQ RECEIVED",
            "MODIFY PENDING", # Order modification pending
            "CANCEL PENDING"  # Order cancellation pending (still active until cancelled)
        ]
        
        # Convert action to transaction type for comparison
        target_transaction_type = "BUY" if action.lower() == "buy" else "SELL"
        
        # Check for duplicate orders
        for order in orders:
            # Check if this order matches our criteria
            if (order.get("tradingsymbol") == tradingsymbol and
                order.get("exchange") == segment and
                order.get("transaction_type") == target_transaction_type and
                order.get("status") in pending_statuses):
                
                # Found a duplicate pending order
                order_details = (
                    f"Order ID: {order.get('order_id')}, "
                    f"Type: {order.get('order_type')}, "
                    f"Status: {order.get('status')}, "
                    f"Quantity: {order.get('quantity')}, "
                    f"Price: {order.get('price', 'Market')}"
                )
                
                logger.warning(
                    f"🔄 Duplicate pending order found for {tradingsymbol} {action.upper()}: {order_details}"
                )
                
                return True, order_details
        
        # No duplicate found
        logger.debug(f"✅ No duplicate pending orders found for {tradingsymbol} {action.upper()}")
        return False, None
        
    except Exception as e:
        logger.error(f"❌ Error checking existing orders for {tradingsymbol}: {e}", exc_info=True)
        # On error, allow the order to proceed (fail-safe approach)
        return False, f"Error checking orders: {str(e)}"
        
def place_order(
    kite: KiteConnect,
    tradingsymbol: str,
    action: str,
    price: float,
    segment: str,
    quantity: int = 1
) -> Tuple[Optional[str], Optional[str]]:
    """
    Place a MARKET order via the KiteConnect API with duplicate order checking and market protection fallback.

    Features:
    - Duplicate order prevention: Checks for existing pending orders
    - Market protection fallback: Automatically retries with protection for illiquid ETFs
    - Comprehensive error handling and logging

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
        
    Fallback Logic:
        1. Attempts regular MARKET order first
        2. If "illiquid ETF" error occurs, retries with market protection
        3. Returns appropriate success/error response
    """
    try:
        # 🔍 STEP 1: Check for existing pending orders
        has_duplicate, order_details = check_existing_pending_orders(
            kite, tradingsymbol, action, segment, quantity
        )
        
        if has_duplicate:
            error_msg = f"Duplicate order prevented: Similar pending order already exists. {order_details}"
            logger.warning(f"🚫 {error_msg}")
            return (None, error_msg)
        
        # 🚀 STEP 2: Proceed with order placement
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
        
        # 📝 Log order attempt
        logger.info(f"📤 Placing {action.upper()} order for {quantity} units of {tradingsymbol} on {segment}")
        
        try:
            # 🎯 ATTEMPT 1: Try placing regular MARKET order
            order_id = kite.place_order(**order_params)
            logger.info(f"✅ {action.upper()} order placed successfully for {tradingsymbol}. Order ID: {order_id}")
            return (order_id, None)
            
        except Exception as market_order_error:
            error_message = str(market_order_error).lower()
            
            # 🛡️ CHECK FOR ILLIQUID ETF ERROR
            if ("market orders are blocked for illiquid etfs" in error_message or 
                "market protection" in error_message or
                "illiquid" in error_message):
                
                logger.warning(f"⚠️ Market order blocked for illiquid ETF {tradingsymbol}: {market_order_error}")
                logger.info(f"🔄 Retrying with market protection enabled for {tradingsymbol}")
                
                try:                    
                    ltp = price
                    
                    buffer_multiplier = 0.005  # 0.5% buffer
                    if action.upper() == "BUY":
                        protected_price = ltp * (1 + buffer_multiplier)
                    elif action.upper() == "SELL":
                        protected_price = ltp * (1 - buffer_multiplier)
                    
                    # 🛡️ ATTEMPT 2: Retry with market protection enabled
                    order_params_with_protection = order_params.copy()
                    
                    # Add market protection parameters
                    # Market protection typically uses a small percentage buffer (e.g., 3-5%)
                    order_params_with_protection.update({
                        "order_type": kite.ORDER_TYPE_LIMIT,     # Instead of MARKET
                        "price": protected_price,                # A price derived from LTP + buffer
                        "validity": kite.VALIDITY_DAY,  # Ensure day validity
                        "disclosed_quantity": 0,       # No disclosed quantity
                        "tag": "market_protection"     # Tag to identify protected orders
                    })
                    
                    # For market protection, we need to get current market price and set a buffer
                    # This is handled internally by Zerodha when market protection is enabled
                    logger.info(f"🛡️ Placing MARKET order with protection for {tradingsymbol}")
                    
                    protected_order_id = kite.place_order(**order_params_with_protection)
                    
                    logger.info(f"✅ {action.upper()} order with market protection placed successfully for {tradingsymbol}. Order ID: {protected_order_id}")
                    logger.info(f"🛡️ Market protection applied to prevent excessive slippage")
                    
                    return (protected_order_id, None)
                    
                except Exception as protection_error:
                    # If market protection also fails, log both errors
                    logger.error(f"❌ Market protection order also failed for {tradingsymbol}: {protection_error}")
                    logger.error(f"❌ Original error: {market_order_error}")
                    
                    combined_error = (
                        f"Both regular and protected market orders failed. "
                        f"Original: {market_order_error}. "
                        f"Protected: {protection_error}"
                    )
                    return (None, combined_error)
            else:
                # Re-raise the original exception if it's not related to illiquid ETFs
                raise market_order_error
        
    except Exception as e:
        logger.error(f"❌ Error placing order for {tradingsymbol}: {e}", exc_info=True)
        return (None, str(e))
