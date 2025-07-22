from fastapi import FastAPI, Request, HTTPException, Query, Form
from fastapi.responses import JSONResponse, HTMLResponse, PlainTextResponse
from fastapi import status
from contextlib import asynccontextmanager
from pydantic import BaseModel
import logging
import os
import asyncio
from datetime import datetime, timedelta, timezone
from orders import place_order, get_top_3_futures_from_tv_symbol
from utils import zerodha_login
from dotenv import load_dotenv
import pytz
from dateutil import parser as dtparser
from logging.handlers import RotatingFileHandler
import pandas as pd
from redis_utils import is_duplicate, set_instrument_cache, get_instrument_cache


# Load environment variables
load_dotenv()

# File paths and secrets from env
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET")
REQUEST_TOKEN_FILE = os.getenv("REQUEST_TOKEN_PATH", "request_token.txt")
ACCESS_TOKEN_FILE = os.getenv("ACCESS_TOKEN_PATH", "access_token.txt")
API_SECRET = os.getenv("KITE_API_SECRET")
API_KEY = os.getenv("KITE_API_KEY")

# --- Startup validation for critical environment variables ---
missing_vars = []
if not WEBHOOK_SECRET:
    missing_vars.append("WEBHOOK_SECRET")
if not API_KEY:
    missing_vars.append("KITE_API_KEY")
if not API_SECRET:
    missing_vars.append("KITE_API_SECRET")
if missing_vars:
    logging.critical(f"Missing required environment variables: {', '.join(missing_vars)}. App will not start.")
    raise RuntimeError(f"Missing required environment variables: {', '.join(missing_vars)}")


kite = zerodha_login()

class WebhookPayload(BaseModel):
    action: str
    symbol: str
    segment: str = "NSE"
    price: float = 0.0
    time: str = None
    quantity: int = 1  # Optional, default 1

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifespan event for FastAPI app. Starts the rollover background task."""
    # --- Build instrument cache for all segments on startup ---
    segments = ["NFO", "MCX"]
    try:
        for seg in segments:
            try:
                # Check cache first
                if get_instrument_cache(seg) is None:
                    instruments = kite.instruments(exchange=seg)
                    df = pd.DataFrame(instruments)
                    set_instrument_cache(seg, df)
                    logging.info(f"[Startup] Built instrument cache for segment {seg}")
                else:
                    logging.info(f"[Startup] Instrument cache for segment {seg} already exists.")
            except Exception as e:
                logging.error(f"[Startup] Failed to build instrument cache for {seg}: {e}", exc_info=True)
    except Exception as e:
        logging.error(f"[Startup] Unexpected error building instrument cache: {e}", exc_info=True)
        
    async def rollover_check():
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
                logging.info(f"rollover_check sleeping for {sleep_seconds/60:.2f} minutes until next weekday 9:25 AM")
                await asyncio.sleep(sleep_seconds)
                
                # --- Refresh instrument cache and create lookups ---
                instrument_lookups = {}
                for seg in segments:
                    try:
                        instruments = kite.instruments(exchange=seg)
                        df = pd.DataFrame(instruments)
                        set_instrument_cache(seg, df)
                        # Create a lookup from tradingsymbol to name for efficient access
                        instrument_lookups[seg] = df.set_index('tradingsymbol')['name'].to_dict()
                        logging.info(f"Refreshed instrument cache and created lookup for segment {seg}")
                    except Exception as e:
                        logging.error(f"Failed to refresh instrument cache for {seg}: {e}", exc_info=True)
                
                # --- Rollover logic starts here ---
                today = datetime.now().date()
                positions = kite.positions()["net"]
                
                # Use a set to track unique base symbols to roll over
                tracked_base_symbols = set()
                for pos in positions:
                    segment = pos.get("exchange")
                    if segment in instrument_lookups and pos.get("product") == "NRML" and  pos.get("quantity") > 0:
                        # Reliably find the base symbol using the instrument lookup
                        base_symbol = instrument_lookups[segment].get(pos["tradingsymbol"])
                        if base_symbol:
                            tracked_base_symbols.add(base_symbol)
                
                logging.info(f"Tracking symbols for rollover: {list(tracked_base_symbols)}")
                
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
                            # This part of the logic correctly finds the existing position
                            # using the full tradingsymbol of the current contract.
                            segment = existing_position["exchange"]
                            #order_id, error = place_order(kite, current_symbol, "sell", 0, segment, quantity=qty_held)
                            if order_id:
                                #order_id, error = place_order(kite, next_symbol, "buy", 0, segment, quantity=qty_held)
                                if order_id:
                                    logging.info(f"✅ Rolled over {base_symbol} from {current_symbol} to {next_symbol}")
                                else:
                                    logging.error(f"❌ Failed to BUY roll over (BUY LEG FAILED) {base_symbol} from {current_symbol} to {next_symbol}")
                            else:
                                logging.error(f"❌ Failed to SELL roll over (SELL LEG FAILED) {base_symbol} from {current_symbol} to {next_symbol}")
                logging.info("Rollover check completed.")
            except Exception as e:
                logging.exception("Rollover scheduler failed")

    asyncio.create_task(rollover_check())
    yield

app = FastAPI(lifespan=lifespan)
import pytz
from datetime import datetime

# Logging setup with rotation
LOG_FILE = "stock_scanner.log"
log_formatter = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
handler = RotatingFileHandler(LOG_FILE, maxBytes=5*1024*1024, backupCount=3, encoding="utf-8")

class ISTFormatter(logging.Formatter):
    def converter(self, timestamp):
        dt = datetime.fromtimestamp(timestamp, pytz.timezone('Asia/Kolkata'))
        return dt
    def formatTime(self, record, datefmt=None):
        dt = self.converter(record.created)
        if datefmt:
            s = dt.strftime(datefmt)
        else:
            s = dt.strftime("%Y-%m-%d %H:%M:%S")
        return s

handler.setFormatter(ISTFormatter(log_formatter))
logging.basicConfig(level=logging.INFO, handlers=[handler])

# Remove any default handlers to avoid duplicate logs
for h in logging.root.handlers[:]:
    if h is not handler:
        logging.root.removeHandler(h)

from fastapi.responses import PlainTextResponse

@app.get("/logs", response_class=PlainTextResponse)
def get_logs(lines: int = 100):
    """Endpoint to fetch the last N lines of the log file for live monitoring."""
    log_path = LOG_FILE
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

@app.get("/token", response_class=HTMLResponse)
def token_form() -> HTMLResponse:
    """Render the Zerodha login form."""
    try:
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
        logging.exception("Failed to generate login URL")
        return HTMLResponse(content=f"<p>Error: An internal error occurred. Please check the server logs.</p>", status_code=500)

from fastapi.responses import HTMLResponse

@app.post("/token")
def save_and_refresh_token(token: str = Form(...)) -> HTMLResponse:
    """Save and refresh Zerodha access token, and validate it."""
    try:
        access_token = kite.generate_session(token, api_secret=API_SECRET)["access_token"]
        with open(ACCESS_TOKEN_FILE, "w") as f:
            f.write(access_token)
        # Validate token by making a simple API call
        try:
            kite.set_access_token(access_token)
            profile = kite.profile()  # Will raise if invalid
            logging.info("Access token validated and saved successfully.")
            return HTMLResponse(content="<b>Token is valid! Login successful.</b>", status_code=200)
        except Exception as ve:
            logging.error(f"Token saved but validation failed: {ve}")
            return HTMLResponse(content=f"<b>Invalid token:</b> An internal error occurred. Please check the server logs.", status_code=400)
    except Exception as e:
        logging.exception("Failed to save and refresh token")
        return HTMLResponse(content=f"<b>Error:</b> An internal error occurred. Please check the server logs.", status_code=500)


@app.post("/webhook", response_model=None)
async def webhook(payload: WebhookPayload, token: str = Query(...)) -> JSONResponse:
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
        # Rollover logic
        if segment == "NFO" or segment == "MCX":
            active_symbol = tv_symbol[:-1] if tv_symbol.endswith("!") else tv_symbol
            if( active_symbol.endswith("1") or active_symbol.endswith("2") or active_symbol.endswith("3")):
                active_symbol = active_symbol[:-1]
            try:
                # Always select contract dynamically
                contracts = get_top_3_futures_from_tv_symbol(tv_symbol, kite, segment)
                if not contracts:
                    raise HTTPException(status_code=404, detail=f"No futures contracts found for {tv_symbol}")
                # Enhanced logic: select next contract if expiry is within 7 days
                expiry = contracts[0].get('expiry')
                next_tradingsymbol = None
                if expiry:
                    # expiry could be a datetime or string, handle both
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
                        next_tradingsymbol = contracts[1]['tradingsymbol'] if len(contracts) > 1 else contracts[0]['tradingsymbol']
                    else:
                        next_tradingsymbol = contracts[0]['tradingsymbol']
                else:
                    next_tradingsymbol = contracts[0]['tradingsymbol']
                tradingsymbol = next_tradingsymbol
            except Exception as e:
                logging.error(f"Error fetching active contract: {e}")
                raise HTTPException(status_code=500, detail="Error fetching active contract. Please check the server logs.")
        else:
            tradingsymbol = tv_symbol
        try:
            if segment == "NFO" or segment == "MCX":
                positions = kite.positions()["net"]
                existing_position = next((p for p in positions if p["tradingsymbol"] == tradingsymbol and p["exchange"] == segment), None)
                qty_held = existing_position["quantity"] if existing_position else 0
            elif segment == "NSE":
                holdings = kite.holdings()
                existing_position = next((h for h in holdings if h["tradingsymbol"] == tradingsymbol), None)
                qty_held = existing_position["quantity"] if existing_position else 0
                t1_qty = existing_position["t1_quantity"] if existing_position and "t1_quantity" in existing_position else 0
                if qty_held == 0 and t1_qty > 0:
                    # Allow selling T1 quantity (BTST)
                    qty_held = t1_qty
                if qty_held == 0:
                    positions = kite.positions()["net"]
                    existing_position = next((p for p in positions if p["tradingsymbol"] == tradingsymbol and p["exchange"] == segment), None)
                    qty_held = existing_position["quantity"] if existing_position else 0
            else:
                qty_held = 0
        except Exception as e:
            logging.error(f"Error fetching positions/holdings: {e}")
            raise HTTPException(status_code=500, detail="Error fetching positions/holdings. Please check the server logs.")
        # Get lot size for futures contracts
        lot_size = 1  # Default lot size for non-futures
        if segment in ["NFO", "MCX"]:
            # Find the contract to get its lot size
            contracts = get_top_3_futures_from_tv_symbol(tv_symbol, kite, segment)
            if contracts:
                lot_size = contracts[0].get('lot_size', 1)
            # Calculate total quantity (lots * lot_size)
            total_quantity = int(quantity * lot_size)
        else:
            total_quantity = quantity
        if action == "buy":
            if qty_held > 0:
                logging.info(f"Already holding {tradingsymbol}. Skipping buy.")
                return JSONResponse(content={"status": "already holding, buy skipped"}, status_code=200)
            else:
                order_id, error = place_order(kite, tradingsymbol, action, price, segment, total_quantity)
                if order_id:
                    logging.info(f"Buy order placed for {total_quantity} units of {tradingsymbol} (lot size: {lot_size})")
                    return JSONResponse(content={"status": "buy order placed", "order_id": order_id, "quantity": total_quantity}, status_code=200)
                elif error:
                    logging.error(f"Buy order failed: {error}")
                    return JSONResponse(content={"status": "buy order failed", "error": "Order failed. Please check the server logs."}, status_code=200)
        elif action == "sell":
            if qty_held <= 0:
                logging.info(f"No holdings for {tradingsymbol}. Skipping sell.")
                return JSONResponse(content={"status": "no holdings, sell skipped"}, status_code=200)
            else:
                # For selling, use the actual quantity held
                sell_quantity = min(total_quantity, abs(qty_held)) if qty_held > 0 else total_quantity
                order_id, error = place_order(kite, tradingsymbol, action, price, segment, sell_quantity)
                if order_id:
                    logging.info(f"Sell order placed for {sell_quantity} units of {tradingsymbol}")
                    return JSONResponse(content={"status": "sell order placed", "order_id": order_id, "quantity": sell_quantity}, status_code=200)
                elif error:
                    logging.error(f"Sell order failed: {error}")
                    return JSONResponse(content={"status": "sell order failed", "error": "Order failed. Please check the server logs."}, status_code=200)

    except HTTPException as he:
        raise he
    except Exception as e:
        logging.exception("Error processing webhook.")
        # Always return 200 OK to avoid TradingView retries, but log the error for review
        return JSONResponse(status_code=200, content={"status": "error", "message": "An internal error occurred. Please check the server logs."})
