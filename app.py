from fastapi import FastAPI, Request, HTTPException, Query, Form, Header
from fastapi.responses import JSONResponse, HTMLResponse, PlainTextResponse
from fastapi import status
from contextlib import asynccontextmanager
from pydantic import BaseModel
import pandas as pd
import logging
import os
import asyncio
from datetime import datetime, timedelta, timezone
from orders import place_order, get_top_3_futures_from_tv_symbol
from utils import zerodha_login_multi
from dotenv import load_dotenv
from memory_manager import memory_manager, cleanup_dataframes, force_gc
from logging_config import setup_logging
from redis_utils import get_instrument_cache, set_instrument_cache, is_duplicate
from dataframe_utils import (
    SafeDataFrameOperations, 
    dataframe_operation_context,
    df_memory_tracker,
    cleanup_large_dataframes
)
import pytz
from dateutil import parser as dtparser
import json
from typing import List, Optional, Dict, Any, Union, Callable
from fastapi.responses import FileResponse
import uuid  # For generating request IDs
import functools
import contextvars
import time  # For performance monitoring

# Setup centralized logging to prevent duplicate handlers
setup_logging()
logger = logging.getLogger(__name__)

# Request ID context
request_id = contextvars.ContextVar('request_id', default=str(uuid.uuid4()))

def log_with_request_id(level: str, message: str, **kwargs):
    """Helper function to log messages with request ID"""
    log_msg = f"[ReqID: {request_id.get()}] {message}"
    extra = {'request_id': request_id.get(), **kwargs}
    
    if level.upper() == 'DEBUG':
        logger.debug(log_msg, extra=extra)
    elif level.upper() == 'INFO':
        logger.info(log_msg, extra=extra)
    elif level.upper() == 'WARNING':
        logger.warning(log_msg, extra=extra)
    elif level.upper() == 'ERROR':
        logger.error(log_msg, extra=extra, exc_info=kwargs.get('exc_info', False))
    elif level.upper() == 'CRITICAL':
        logger.critical(log_msg, extra=extra, exc_info=kwargs.get('exc_info', False))

def log_operation(operation_name: str):
    """Decorator to log function entry and exit with request ID"""
    def decorator(func):
        @functools.wraps(func)
        async def async_wrapper(*args, **kwargs):
            req_id = request_id.get()
            log_with_request_id('INFO', f"Starting operation: {operation_name}", operation=operation_name)
            try:
                result = await func(*args, **kwargs)
                log_with_request_id('INFO', f"Completed operation: {operation_name}", operation=operation_name)
                return result
            except Exception as e:
                log_with_request_id('ERROR', f"Operation failed: {operation_name} - {str(e)}", 
                                 operation=operation_name, error=str(e), exc_info=True)
                raise
        return async_wrapper
    return decorator

# Load environment variables
load_dotenv()

# File paths and secrets from env
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET")
ACCESS_TOKEN_FILE = os.getenv("ACCESS_TOKEN_PATH", "access_token.txt")
API_SECRET = os.getenv("KITE_API_SECRET")
API_KEY = os.getenv("KITE_API_KEY")

kite1, kite2 = zerodha_login_multi()

class WebhookPayload(BaseModel):
    action: str
    symbol: str
    segment: str = "NSE"
    price: float = 0.0
    time: str = None
    quantity: int = 1  # Optional, default 1

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifespan event for FastAPI app. Starts the rollover background task for both accounts."""
    
    # Store background tasks to prevent memory leaks
    background_tasks = []
    
    # --- Validate Environment Variables ---
    required_vars = [
        'KITE_API_KEY_1', 'KITE_API_SECRET_1', 'ACCESS_TOKEN_PATH_1',
        'KITE_API_KEY_2', 'KITE_API_SECRET_2', 'ACCESS_TOKEN_PATH_2',
        'WEBHOOK_SECRET'
    ]
    missing_vars = [var for var in required_vars if not os.getenv(var)]
    if missing_vars:
        error_msg = f"Missing required environment variables: {', '.join(missing_vars)}"
        logging.critical(error_msg)
        raise RuntimeError(error_msg)

    # --- Check Account Configuration (Not Authentication) ---
    def check_account_config(account_num):
        """Check if account configuration is present (not authentication)."""
        try:
            api_key, api_secret, access_token_file = get_account_credentials(account_num)
            if not api_key or not api_secret:
                return False, f"Missing API key/secret for account {account_num}"
            return True, f"Account {account_num} configuration found"
        except Exception as e:
            return False, f"Account {account_num} configuration error: {str(e)}"

    # Check account configurations (not authentication)
    account1_configured, account1_msg = check_account_config(1)
    account2_configured, account2_msg = check_account_config(2)

    if not (account1_configured or account2_configured):
        error_msg = "❌ Critical: Neither account is properly configured. Check environment variables."
        logging.critical(error_msg)
        raise RuntimeError(error_msg)
    elif not account1_configured:
        logging.warning(f"⚠️  Account 1 configuration issue: {account1_msg}")
        logging.info("Account 1 will need authentication via /token/1 endpoint")
    elif not account2_configured:
        logging.warning(f"⚠️  Account 2 configuration issue: {account2_msg}")
        logging.info("Account 2 will need authentication via /token/2 endpoint")
    else:
        logging.info("✅ Both accounts configured successfully")
        logging.info("Accounts will need authentication via /token/1 and /token/2 endpoints")

    # --- Skip instrument cache building at startup (will be built on first webhook) ---
    logging.info("[Startup] Skipping instrument cache building - will be populated on first webhook request")
    logging.info("[Startup] Instrument cache will be built when accounts are authenticated and first trade occurs")

    async def rollover_check(kite, account_name):
        segments = ["NFO", "MCX"]
        while True:
            try:
                now = datetime.now()
                # Calculate the next 9:25 AM on a weekday
                next_run = now.replace(hour=9, minute=25, second=0, microsecond=0)
                if now >= next_run:
                    # If already past today's 9:25 AM, schedule for next weekday
                    next_run += timedelta(days=1)
                while next_run.weekday() >= 5:  # 5=Saturday, 6=Sunday
                    next_run += timedelta(days=1)

                # Sleep until next_run
                sleep_seconds = (next_run - now).total_seconds()
                logging.info(f"{account_name} rollover_check sleeping for {sleep_seconds/60:.2f} minutes until next weekday 9:25 AM")
                await asyncio.sleep(sleep_seconds)

                # --- Refresh instrument cache and create lookups (using kite1 only) ---
                instrument_lookups = {}
                
                # Use DataFrame context manager for automatic cleanup
                with memory_manager.create_dataframe_context() as df_ctx:
                    for seg in segments:
                        try:
                            df = get_instrument_cache(seg)
                            if df is not None:
                                # Track DataFrame for automatic cleanup
                                df_ctx.track(df)
                                
                                # Create lookup dictionary from DataFrame
                                df_indexed = df.set_index('tradingsymbol')
                                df_ctx.track(df_indexed)  # Track indexed DataFrame too
                                
                                instrument_lookups[seg] = df_indexed['name'].to_dict()
                                logging.info(f"{account_name} loaded instrument lookup for segment {seg} ({len(instrument_lookups[seg])} instruments)")
                            else:
                                logging.warning(f"{account_name} instrument cache missing for segment {seg}")
                        except Exception as e:
                            logging.error(f"{account_name} failed to load instrument cache for {seg}: {e}", exc_info=True)
                
                # DataFrames are automatically cleaned up when exiting the context
                # Force garbage collection after processing all segments
                force_gc()

                # --- Rollover logic starts here ---
                today = datetime.now().date()
                
                # Check if account is authenticated before making API calls
                try:
                    positions = kite.positions()["net"]
                except Exception as e:
                    logging.warning(f"{account_name} rollover check skipped - account not authenticated: {e}")
                    continue  # Skip this iteration and try again next time

                # Use a set to track unique base symbols to roll over
                tracked_base_symbols = set()
                for pos in positions:
                    segment = pos.get("exchange")
                    if segment in instrument_lookups and pos.get("product") == "NRML" and  pos.get("quantity") > 0:
                        # Reliably find the base symbol using the instrument lookup
                        base_symbol = instrument_lookups[segment].get(pos["tradingsymbol"])
                        if base_symbol:
                            tracked_base_symbols.add(base_symbol)

                logging.info(f"{account_name} tracking symbols for rollover: {list(tracked_base_symbols)}")

                for base_symbol in sorted(list(tracked_base_symbols)):
                    contracts = get_top_3_futures_from_tv_symbol(base_symbol + "!", kite, 'NFO')
                    if not contracts:
                        contracts = get_top_3_futures_from_tv_symbol(base_symbol + "!", kite, 'MCX')
                    if len(contracts) < 2:
                        continue

                    current_contract = contracts[0]
                    next_contract = contracts[1]

                    expiry = current_contract['expiry']

                    if isinstance(expiry, int):
                        # Milliseconds to UTC date (safe & modern)
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

                    if 0 < days_left <= 7 and today.weekday() < 5:
                        current_symbol = current_contract['tradingsymbol']
                        next_symbol = next_contract['tradingsymbol']

                        existing_position = next((p for p in positions if p["tradingsymbol"] == current_symbol and p["exchange"] in ["NFO", "MCX"]), None)

                        qty_held = existing_position["quantity"] if existing_position else 0

                        if qty_held > 0:
                            segment = existing_position["exchange"]
                            #order_id, error = place_order(kite, current_symbol, "sell", 0, segment, quantity=qty_held)
                            if order_id:
                                #order_id2, error2 = place_order(kite, next_symbol, "buy", 0, segment, quantity=qty_held)
                                if order_id2:
                                    logging.info(f"✅ {account_name} Rolled over {base_symbol} from {current_symbol} to {next_symbol}")
                                else:
                                    logging.error(f"❌ {account_name} Failed to BUY roll over (BUY LEG FAILED) {base_symbol} from {current_symbol} to {next_symbol}: {error2}")
                            else:
                                logging.error(f"❌ {account_name} Failed to SELL roll over (SELL LEG FAILED) {base_symbol} from {current_symbol} to {next_symbol}: {error}")
                logging.info(f"{account_name} Rollover check completed.")
            except Exception as e:
                logging.exception(f"{account_name} Rollover scheduler failed")
                # Sleep to prevent tight error loop
                await asyncio.sleep(60)  # Wait 1 minute before retrying

    # Start rollover checks for both accounts and store task references
    try:
        rollover_task1 = asyncio.create_task(rollover_check(kite1, "Account1"))
        rollover_task2 = asyncio.create_task(rollover_check(kite2, "Account2"))
        
        # Start memory monitoring task
        memory_task = asyncio.create_task(memory_manager.monitor_memory(interval_seconds=300))
        
        background_tasks.extend([rollover_task1, rollover_task2, memory_task])
        
        logging.info("Background rollover and memory monitoring tasks started successfully")
        yield
        
    finally:
        # Cleanup: Cancel all background tasks on shutdown
        logging.info("Shutting down background tasks...")
        for task in background_tasks:
            if not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    logging.info(f"Task {task.get_name() if hasattr(task, 'get_name') else 'unnamed'} cancelled successfully")
                except Exception as e:
                    logging.error(f"Error cancelling task: {e}")
        
        # Wait a bit for graceful shutdown
        await asyncio.sleep(0.1)
        logging.info("All background tasks cleaned up")

app = FastAPI(lifespan=lifespan)

# Note: Logging is now handled by setup_logging() called at the top
# The duplicate logging configuration has been removed to prevent memory leaks

from fastapi.responses import PlainTextResponse

@app.get("/logs", response_class=PlainTextResponse)
def get_logs(lines: int = 100):
    """Endpoint to fetch the last N lines of the log file for live monitoring."""
    log_path = "stock_scanner.log"  # Use the same log file as logging_config.py
    if not os.path.exists(log_path):
        return PlainTextResponse("Log file not found.", status_code=404)
    try:
        with open(log_path, "r", encoding="utf-8", errors="ignore") as f:
            all_lines = f.readlines()
        # Return only the last N lines
        output = "".join(all_lines[-lines:])
        return PlainTextResponse(output, status_code=200)
    except Exception as e:
        logging.exception("Error reading log file")
        return PlainTextResponse("Error reading log file. Please check the server logs.", status_code=500)

@app.get("/performance", response_class=JSONResponse)
def get_performance_stats():
    """Endpoint to get webhook processing performance statistics."""
    try:
        from performance_optimizations import perf_monitor, perf_optimizer
        
        # Get performance stats
        perf_stats = perf_monitor.get_stats()
        
        # Get cache statistics
        cache_stats = perf_optimizer.get_cache_stats()
        
        # Combine both sets of statistics
        combined_stats = {
            **perf_stats,
            "cache_performance": cache_stats
        }
        
        return JSONResponse(content=combined_stats, status_code=200)
    except Exception as e:
        logging.exception("Error getting performance stats")
        return JSONResponse(content={"error": "Failed to get performance stats"}, status_code=500)

@app.get("/memory", response_class=JSONResponse)
def get_memory_stats():
    """Endpoint to get memory usage and DataFrame statistics."""
    try:
        # Get system memory usage
        current_memory = memory_manager.get_memory_usage()
        
        # Get DataFrame memory report
        df_report = df_memory_tracker.get_memory_report()
        
        # Get memory manager statistics
        memory_stats = {
            "system_memory_mb": round(current_memory, 2),
            "memory_threshold_mb": memory_manager.memory_threshold_mb,
            "threshold_exceeded": current_memory > memory_manager.memory_threshold_mb,
            "dataframe_memory": df_report
        }
        
        return JSONResponse(content=memory_stats, status_code=200)
    except Exception as e:
        logging.exception("Error getting memory stats")
        return JSONResponse(content={"error": "Failed to get memory stats"}, status_code=500)

@app.post("/memory/cleanup", response_class=JSONResponse)
def force_memory_cleanup():
    """Endpoint to force memory cleanup and garbage collection."""
    try:
        initial_memory = memory_manager.get_memory_usage()
        
        # Cleanup large DataFrames
        cleanup_large_dataframes(threshold_mb=25)
        
        # Force garbage collection
        collected = force_gc()
        
        final_memory = memory_manager.get_memory_usage()
        memory_freed = initial_memory - final_memory
        
        cleanup_stats = {
            "initial_memory_mb": round(initial_memory, 2),
            "final_memory_mb": round(final_memory, 2),
            "memory_freed_mb": round(memory_freed, 2),
            "gc_objects_collected": collected,
            "cleanup_successful": memory_freed > 0
        }
        
        logging.info(f"Manual memory cleanup completed: freed {memory_freed:.1f}MB")
        return JSONResponse(content=cleanup_stats, status_code=200)
    except Exception as e:
        logging.exception("Error during memory cleanup")
        return JSONResponse(content={"error": "Failed to perform memory cleanup"}, status_code=500)

# Add helper to get account-specific credentials

def get_account_credentials(account_num):
    if account_num == 1:
        api_key = os.getenv("KITE_API_KEY_1")
        api_secret = os.getenv("KITE_API_SECRET_1")
        access_token_file = os.getenv("ACCESS_TOKEN_PATH_1", "access_token1.txt")
    elif account_num == 2:
        api_key = os.getenv("KITE_API_KEY_2")
        api_secret = os.getenv("KITE_API_SECRET_2")
        access_token_file = os.getenv("ACCESS_TOKEN_PATH_2", "access_token2.txt")
    else:
        raise ValueError("Invalid account number")
    return api_key, api_secret, access_token_file

# Account-specific login form
from fastapi.responses import HTMLResponse

@app.get("/token/{account_num}", response_class=HTMLResponse)
def token_form_account(account_num: int) -> HTMLResponse:
    """Render the Zerodha login form for a specific account."""
    try:
        api_key, api_secret, access_token_file = get_account_credentials(account_num)
        from kiteconnect import KiteConnect
        kite = KiteConnect(api_key=api_key)
        login_url = kite.login_url()
        return HTMLResponse(content=f"""
        <html>
            <body>
                <p>1. Click the link below to login to Zerodha and get your <b>request_token</b>:</p>
                <a href=\"{login_url}\" target=\"_blank\">{login_url}</a>
                <p>2. Paste the request_token below:</p>
                <form method=\"post\">
                  <input type=\"text\" name=\"token\" size=\"50\"/><br><br>
                  <input type=\"submit\" value=\"Submit\"/>
                </form>
            </body>
        </html>
        """, status_code=200)
    except Exception as e:
        logging.exception(f"Failed to generate login URL for account {account_num}")
        return HTMLResponse(content=f"<p>Error: An internal error occurred. Please check the server logs.</p>", status_code=500)

@app.post("/token/{account_num}")
def save_and_refresh_token_account(account_num: int, token: str = Form(...)) -> HTMLResponse:
    """Save and refresh Zerodha access token for a specific account, and validate it."""
    global kite1, kite2  # Access global kite objects
    
    try:
        api_key, api_secret, access_token_file = get_account_credentials(account_num)
        from kiteconnect import KiteConnect
        kite = KiteConnect(api_key=api_key)
        access_token = kite.generate_session(token, api_secret=api_secret)["access_token"]
        with open(access_token_file, "w") as f:
            f.write(access_token)
        
        # Validate token by making a simple API call
        try:
            kite.set_access_token(access_token)
            profile = kite.profile()  # Will raise if invalid
            
            # ✅ UPDATE GLOBAL KITE OBJECTS WITH NEW ACCESS TOKEN
            if account_num == 1:
                kite1.set_access_token(access_token)
                logging.info(f"✅ Global kite1 object updated with new access token for account {account_num}")
            elif account_num == 2:
                kite2.set_access_token(access_token)
                logging.info(f"✅ Global kite2 object updated with new access token for account {account_num}")
            
            logging.info(f"Access token validated and saved successfully for account {account_num}.")
            user_info = f"User: {profile.get('user_name', 'N/A')} ({profile.get('user_id', 'N/A')})"
            return HTMLResponse(content=f"<b>Token is valid! Login successful.</b><br>{user_info}<br>Account {account_num} is now ready for trading.", status_code=200)
        except Exception as ve:
            logging.error(f"Token saved but validation failed for account {account_num}: {ve}")
            return HTMLResponse(content=f"<b>Invalid token:</b> An internal error occurred. Please check the server logs.", status_code=400)
    except Exception as e:
        logging.exception(f"Failed to save and refresh token for account {account_num}")
        return HTMLResponse(content=f"<b>Error:</b> An internal error occurred. Please check the server logs.", status_code=500)

# Keep the old /token endpoint for backward compatibility (account 1)


import asyncio

async def place_order_async(kite, *args, **kwargs):
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, place_order, kite, *args, **kwargs)

@app.post("/webhook", response_model=None)
@log_operation("webhook_request")
async def webhook(
    payload: WebhookPayload, 
    token: str = Query(...),
    x_request_id: Optional[str] = Header(None, alias='X-Request-ID')
) -> JSONResponse:
    # Set or generate request ID
    req_id = x_request_id or str(uuid.uuid4())
    request_id.set(req_id)
    
    log_with_request_id('INFO', 
        'Received webhook request',
        payload=payload.dict(),
        request_id=req_id
    )
    """Webhook endpoint for trading signals."""
    if token != WEBHOOK_SECRET:
        logging.warning("Unauthorized access attempt.")
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")
    timestamp = payload.time
    symbol = payload.symbol
    if not timestamp:
        logging.error("No timestamp provided in payload. Cannot ensure idempotency.")
        return JSONResponse(content={"status": "error", "message": "No timestamp in payload."}, status_code=400)
    if is_duplicate(symbol, timestamp):
        logging.info(f"Duplicate alert received for symbol {symbol} at timestamp {timestamp}. Skipping processing.")
        return JSONResponse(content={"status": "duplicate", "message": "Alert already processed for this symbol and time."}, status_code=200)
    try:
        # Convert payload.time (UTC) to IST for logging
        utc_time = None
        ist_time_str = None
        if payload.time:
            try:
                utc_time = dtparser.parse(payload.time)
                if utc_time.tzinfo is None:
                    utc_time = pytz.utc.localize(utc_time)
                ist_time = utc_time.astimezone(pytz.timezone('Asia/Kolkata'))
                ist_time_str = ist_time.strftime('%Y-%m-%d %H:%M:%S %Z')
            except Exception as e:
                logging.warning(f"Could not parse payload.time: {payload.time} ({e})")
        logging.info(f"Received Webhook: {payload.json()} | payload.time (UTC): {payload.time} | IST: {ist_time_str}")
        action = payload.action
        tv_symbol = payload.symbol
        segment = payload.segment
        price = payload.price
        time_received = payload.time
        # Extract quantity
        quantity = payload.quantity if hasattr(payload, 'quantity') and payload.quantity else 1
        # For NFO/MCX, always force quantity to 1
        if segment in ["NFO", "MCX"]:
            quantity = 1

        if action not in ["buy", "sell"]:
            logging.error(f"Invalid action received: {action}")
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid action")

        # Import performance optimizations
        from performance_optimizations import process_account_optimized, perf_optimizer, perf_monitor
        
        # Clear request-level cache at start of each webhook
        perf_optimizer.clear_request_cache()
        
        webhook_start_time = time.time()

        # Run both accounts in parallel with request ID context
        results = []
        account_tasks = []
        
        # Create tasks with proper request ID context using optimized processing
        for idx, (kite, acc_name) in enumerate([(kite1, "account1"), (kite2, "account2")], 1):
            # Create a task with the optimized account processing
            task = asyncio.create_task(
                process_account_optimized(kite, acc_name, tv_symbol, segment, action, price, quantity)
            )
            task.acc_name = acc_name
            account_tasks.append(task)
            
        # Wait for all tasks to complete
        if account_tasks:
            done, _ = await asyncio.wait(account_tasks, return_when=asyncio.ALL_COMPLETED)
            for task in done:
                try:
                    result = task.result()
                    log_with_request_id('INFO', 
                        f"Account {task.acc_name} processing completed",
                        account=task.acc_name,
                        result=result
                    )
                    results.append(result)
                except Exception as e:
                    log_with_request_id('ERROR', 
                        f"Account {task.acc_name} processing failed: {str(e)}",
                        account=task.acc_name,
                        error=str(e),
                        exc_info=True
                    )
                    results.append({"status": "error", "error": str(e), "account": task.acc_name})
        # Calculate total webhook processing time
        total_processing_time = (time.time() - webhook_start_time) * 1000
        
        # Record performance metrics
        perf_monitor.record_request(total_processing_time, req_id)
        
        response = {
            "account1": results[0],
            "account2": results[1],
            "total_processing_time_ms": round(total_processing_time, 2),
            "request_id": req_id
        }
        
        # Log performance summary
        log_with_request_id('INFO', 
            f"Webhook processing completed in {total_processing_time:.1f}ms",
            total_time_ms=total_processing_time,
            account1_time_ms=results[0].get('processing_time_ms', 0),
            account2_time_ms=results[1].get('processing_time_ms', 0)
        )
        
        return JSONResponse(content=response, status_code=200)

    except HTTPException as he:
        raise he
    except Exception as e:
        logging.exception("Error processing webhook.")
        # Always return 200 OK to avoid TradingView retries, but log the error for review
        return JSONResponse(status_code=200, content={"status": "error", "message": "An internal error occurred. Please check the server logs."})
