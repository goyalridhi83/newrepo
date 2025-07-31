"""
Performance optimizations for app.py to reduce slippage in dual-account trading.
These optimizations focus on reducing API calls and improving parallel processing.
"""

import asyncio
import time
from typing import Dict, Any, Optional, Tuple
import logging

logger = logging.getLogger(__name__)

class PerformanceOptimizer:
    """Optimizations to reduce webhook processing latency."""
    
    def __init__(self):
        self.contract_cache = {}  # Cache contracts within request only
        # Cache hit/miss statistics (for monitoring only)
        self.cache_stats = {
            'contract_hits': 0,
            'contract_misses': 0
        }
        
    def clear_request_cache(self):
        """Clear cache at the start of each webhook request."""
        self.contract_cache.clear()
        # Note: cache_stats are NOT cleared - they accumulate for monitoring
        
    def get_cache_stats(self):
        """Get cache hit/miss statistics."""
        total_contract = self.cache_stats['contract_hits'] + self.cache_stats['contract_misses']
        
        return {
            'contract_cache': {
                'hits': self.cache_stats['contract_hits'],
                'misses': self.cache_stats['contract_misses'],
                'hit_rate': round(self.cache_stats['contract_hits'] / total_contract * 100, 1) if total_contract > 0 else 0
            }
        }

# Global optimizer instance
perf_optimizer = PerformanceOptimizer()

async def get_contracts_cached(tv_symbol: str, kite, segment: str) -> list:
    """Get contracts with request-level caching to avoid duplicate API calls."""
    cache_key = f"{tv_symbol}_{segment}"
    
    if cache_key in perf_optimizer.contract_cache:
        perf_optimizer.cache_stats['contract_hits'] += 1
        logger.debug(f"Contract cache hit: {cache_key}")
        return perf_optimizer.contract_cache[cache_key]
    
    # Cache miss - fetch from API
    perf_optimizer.cache_stats['contract_misses'] += 1
    logger.debug(f"Contract cache miss: {cache_key}")
    
    try:
        # Import here to avoid circular imports
        from orders import get_top_3_futures_from_tv_symbol
        
        contracts = get_top_3_futures_from_tv_symbol(tv_symbol, kite, segment)
        
        # Cache the result (contracts are simple dictionaries, not DataFrames)
        perf_optimizer.contract_cache[cache_key] = contracts
        
        logger.debug(f"Cached {len(contracts)} contracts for {cache_key}")
        return contracts
        
    except Exception as e:
        logger.error(f"Error fetching contracts for {cache_key}: {e}")
        # Return empty list on error to prevent crashes
        return []

async def get_positions_and_holdings_direct(kite, segment: str, tradingsymbol: str) -> Tuple[int, Dict]:
    """Get positions and holdings directly from API without caching."""
    
    # Variables for DataFrame cleanup
    holdings_df = None
    positions_df = None
    
    try:
        if segment in ["NFO", "MCX"]:
            # Only need positions for futures - with timeout handling
            try:
                # Add explicit timeout to prevent hanging
                positions_data = await asyncio.wait_for(
                    asyncio.get_event_loop().run_in_executor(
                        None, lambda: kite.positions()["net"]
                    ),
                    timeout=10.0  # 10 second timeout
                )
                
                # Convert to DataFrame for efficient processing if needed
                if len(positions_data) > 100:  # Only use DataFrame for large datasets
                    import pandas as pd
                    positions_df = pd.DataFrame(positions_data)
                    matching_positions = positions_df[
                        (positions_df['tradingsymbol'] == tradingsymbol) & 
                        (positions_df['exchange'] == segment)
                    ]
                    existing_position = matching_positions.iloc[0].to_dict() if not matching_positions.empty else None
                else:
                    # Use simple iteration for small datasets
                    existing_position = next(
                        (p for p in positions_data if p["tradingsymbol"] == tradingsymbol and p["exchange"] == segment), 
                        None
                    )
                
                qty_held = existing_position["quantity"] if existing_position else 0
                result = (qty_held, existing_position or {})
                
            except asyncio.TimeoutError:
                logger.error(f"Timeout fetching positions for {segment} - API took longer than 10 seconds")
                result = (0, {})
            except Exception as e:
                logger.error(f"Error fetching positions for {segment}: {e}")
                result = (0, {})
            
        elif segment == "NSE":
            # Need both holdings and positions for NSE - fetch in parallel with timeout handling
            try:
                async def get_holdings():
                    return await asyncio.wait_for(
                        asyncio.get_event_loop().run_in_executor(None, kite.holdings),
                        timeout=10.0  # 10 second timeout
                    )
                
                async def get_positions():
                    return await asyncio.wait_for(
                        asyncio.get_event_loop().run_in_executor(None, lambda: kite.positions()["net"]),
                        timeout=10.0  # 10 second timeout
                    )
                
                # Parallel execution with timeout protection
                holdings_data, positions_data = await asyncio.gather(get_holdings(), get_positions())
                
                # Process holdings efficiently
                if len(holdings_data) > 50:  # Use DataFrame for larger datasets
                    import pandas as pd
                    holdings_df = pd.DataFrame(holdings_data)
                    matching_holdings = holdings_df[holdings_df['tradingsymbol'] == tradingsymbol]
                    existing_position = matching_holdings.iloc[0].to_dict() if not matching_holdings.empty else None
                else:
                    # Simple iteration for small datasets
                    existing_position = next((h for h in holdings_data if h["tradingsymbol"] == tradingsymbol), None)
                
                qty_held = existing_position["quantity"] if existing_position else 0
                t1_qty = existing_position["t1_quantity"] if existing_position and "t1_quantity" in existing_position else 0
                
                if qty_held == 0 and t1_qty > 0:
                    qty_held = t1_qty
                    
                # If still no holdings, check positions
                if qty_held == 0:
                    if len(positions_data) > 100:  # Use DataFrame for larger datasets
                        import pandas as pd
                        if positions_df is None:  # Avoid recreating if already exists
                            positions_df = pd.DataFrame(positions_data)
                        matching_positions = positions_df[
                            (positions_df['tradingsymbol'] == tradingsymbol) & 
                            (positions_df['exchange'] == segment)
                        ]
                        existing_position = matching_positions.iloc[0].to_dict() if not matching_positions.empty else None
                    else:
                        existing_position = next(
                            (p for p in positions_data if p["tradingsymbol"] == tradingsymbol and p["exchange"] == segment), 
                            None
                        )
                    qty_held = existing_position["quantity"] if existing_position else 0
                    
                result = (qty_held, existing_position or {})
                
            except asyncio.TimeoutError:
                logger.error(f"Timeout fetching holdings/positions for {segment} - API took longer than 10 seconds")
                result = (0, {})
            except Exception as e:
                logger.error(f"Error fetching holdings/positions for {segment}: {e}")
                result = (0, {})
        else:
            result = (0, {})
        
        return result
        
    finally:
        # Always cleanup DataFrames if they were created
        dataframes_to_cleanup = []
        if holdings_df is not None:
            dataframes_to_cleanup.append(holdings_df)
        if positions_df is not None:
            dataframes_to_cleanup.append(positions_df)
            
        if dataframes_to_cleanup:
            try:
                from memory_manager import cleanup_dataframes
                cleanup_dataframes(*dataframes_to_cleanup)
            except ImportError:
                # Fallback cleanup
                for df in dataframes_to_cleanup:
                    try:
                        del df
                    except:
                        pass
        
        # Clear variables
        holdings_df = None
        positions_df = None

async def process_account_optimized(kite, account_name: str, tv_symbol: str, segment: str, 
                                  action: str, price: float, quantity: int) -> Dict[str, Any]:
    """Optimized account processing with reduced API calls and parallel execution."""
    
    start_time = time.time()
    
    try:
        # Step 1: Get tradingsymbol (different logic for BUY vs SELL)
        if segment in ["NFO", "MCX"]:
            active_symbol = tv_symbol[:-1] if tv_symbol.endswith("!") else tv_symbol
            if active_symbol.endswith(("1", "2", "3")):
                active_symbol = active_symbol[:-1]
            
            contracts = await get_contracts_cached(tv_symbol, kite, segment)
            if not contracts:
                return {"status": "error", "error": f"No futures contracts found for {tv_symbol}"}
            
            if action == "buy":
                # BUY: Use rollover logic to select appropriate contract
                expiry = contracts[0].get('expiry')
                if expiry:
                    from datetime import datetime, timezone
                    import pandas as pd
                    
                    if isinstance(expiry, int):
                        expiry = datetime.fromtimestamp(expiry / 1000, tz=timezone.utc).date()
                    elif isinstance(expiry, str):
                        try:
                            expiry = datetime.strptime(expiry, "%Y-%m-%d").date()
                        except ValueError:
                            expiry = datetime.fromisoformat(expiry).date()
                    elif isinstance(expiry, datetime):
                        expiry = expiry.date()
                    elif isinstance(expiry, pd.Timestamp):
                        expiry = expiry.date()

                    today = datetime.now().date()
                    days_left = (expiry - today).days

                    if days_left <= 7 and today.weekday() < 5:   
                        tradingsymbol = contracts[1]['tradingsymbol'] if len(contracts) > 1 else contracts[0]['tradingsymbol']
                        selected_contract = contracts[1] if len(contracts) > 1 else contracts[0]
                    else:
                        tradingsymbol = contracts[0]['tradingsymbol']
                        selected_contract = contracts[0]
                else:
                    tradingsymbol = contracts[0]['tradingsymbol']
                    selected_contract = contracts[0]
                    
                lot_size = selected_contract.get('lot_size', 1)
                total_quantity = int(quantity * lot_size)
                
                # Get positions/holdings for the selected contract
                qty_held, existing_position = await get_positions_and_holdings_direct(kite, segment, tradingsymbol)
                
            elif action == "sell":
                # SELL: Check which contracts user actually holds
                tradingsymbol = None
                qty_held = 0
                existing_position = {}
                lot_size = 1
                
                # Check all contracts to find which one user holds
                for contract in contracts:
                    contract_symbol = contract['tradingsymbol']
                    contract_qty, contract_position = await get_positions_and_holdings_direct(kite, segment, contract_symbol)
                    
                    if contract_qty > 0:
                        # Found a position - use this contract for selling
                        tradingsymbol = contract_symbol
                        qty_held = contract_qty
                        existing_position = contract_position
                        lot_size = contract.get('lot_size', 1)
                        logger.info(f"{account_name}: Found position in {tradingsymbol}, quantity: {qty_held}")
                        break
                
                if tradingsymbol is None:
                    # No positions found in any contract
                    processing_time = (time.time() - start_time) * 1000
                    logger.info(f"{account_name}: No holdings found in any {active_symbol} contracts. Skipping sell. ({processing_time:.1f}ms)")
                    return {"status": "no holdings, sell skipped", "processing_time_ms": processing_time}
                
                total_quantity = int(quantity * lot_size)
        else:
            # NSE stocks - same logic for both buy and sell
            tradingsymbol = tv_symbol
            lot_size = 1
            total_quantity = quantity
            
            # Get positions/holdings
            qty_held, existing_position = await get_positions_and_holdings_direct(kite, segment, tradingsymbol)
        
        # Step 3: Place order logic (same as before)
        if action == "buy":
            if qty_held > 0:
                processing_time = (time.time() - start_time) * 1000
                logger.info(f"{account_name}: Already holding {tradingsymbol}. Skipping buy. ({processing_time:.1f}ms)")
                return {"status": "already holding, buy skipped", "quantity": qty_held, "processing_time_ms": processing_time}
            else:
                # Import here to avoid circular imports
                from orders import place_order
                order_id, error = await asyncio.get_event_loop().run_in_executor(
                    None, place_order, kite, tradingsymbol, action, price, segment, total_quantity
                )
                processing_time = (time.time() - start_time) * 1000
                if order_id:
                    logger.info(f"{account_name}: Buy order placed for {total_quantity} units of {tradingsymbol} (lot size: {lot_size}) ({processing_time:.1f}ms)")
                    return {"status": "buy order placed", "order_id": order_id, "quantity": total_quantity, "processing_time_ms": processing_time}
                else:
                    logger.error(f"{account_name}: Buy order failed: {error} ({processing_time:.1f}ms)")
                    return {"status": "buy order failed", "error": "Order failed. Please check the server logs.", "processing_time_ms": processing_time}
                    
        elif action == "sell":
            if qty_held <= 0:
                processing_time = (time.time() - start_time) * 1000
                logger.info(f"{account_name}: No holdings for {tradingsymbol}. Skipping sell. ({processing_time:.1f}ms)")
                return {"status": "no holdings, sell skipped", "processing_time_ms": processing_time}
            else:
                sell_quantity = qty_held
                # Import here to avoid circular imports
                from orders import place_order
                order_id, error = await asyncio.get_event_loop().run_in_executor(
                    None, place_order, kite, tradingsymbol, action, price, segment, sell_quantity
                )
                processing_time = (time.time() - start_time) * 1000
                if order_id:
                    logger.info(f"{account_name}: Sell order placed for {sell_quantity} units of {tradingsymbol} ({processing_time:.1f}ms)")
                    return {"status": "sell order placed", "order_id": order_id, "quantity": sell_quantity, "processing_time_ms": processing_time}
                else:
                    logger.error(f"{account_name}: Sell order failed: {error} ({processing_time:.1f}ms)")
                    return {"status": "sell order failed", "error": "Order failed. Please check the server logs.", "processing_time_ms": processing_time}
        
        processing_time = (time.time() - start_time) * 1000
        return {"status": "unknown error", "processing_time_ms": processing_time}
        
    except Exception as e:
        processing_time = (time.time() - start_time) * 1000
        logger.exception(f"{account_name}: Error processing order. ({processing_time:.1f}ms)")
        return {"status": "error", "error": "An internal error occurred. Please check the server logs.", "processing_time_ms": processing_time}

# Performance monitoring
class PerformanceMonitor:
    """Monitor webhook processing performance."""
    
    def __init__(self):
        self.request_times = []
        self.slow_requests = []
        
    def record_request(self, processing_time_ms: float, request_id: str):
        """Record request processing time."""
        self.request_times.append(processing_time_ms)
        
        # Keep only last 100 requests
        if len(self.request_times) > 100:
            self.request_times.pop(0)
            
        # Track slow requests (>500ms)
        if processing_time_ms > 500:
            self.slow_requests.append({
                'request_id': request_id,
                'processing_time_ms': processing_time_ms,
                'timestamp': time.time()
            })
            
        # Keep only last 20 slow requests
        if len(self.slow_requests) > 20:
            self.slow_requests.pop(0)
            
    def get_stats(self) -> Dict[str, Any]:
        """Get performance statistics."""
        if not self.request_times:
            return {"message": "No requests recorded"}
            
        avg_time = sum(self.request_times) / len(self.request_times)
        max_time = max(self.request_times)
        min_time = min(self.request_times)
        
        return {
            "avg_processing_time_ms": round(avg_time, 2),
            "max_processing_time_ms": max_time,
            "min_processing_time_ms": min_time,
            "total_requests": len(self.request_times),
            "slow_requests_count": len(self.slow_requests),
            "recent_slow_requests": self.slow_requests[-5:] if self.slow_requests else []
        }

# Global performance monitor
perf_monitor = PerformanceMonitor()