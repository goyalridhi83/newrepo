from fastapi import FastAPI, Request, HTTPException, Query, Form
from fastapi.responses import JSONResponse, HTMLResponse
from fastapi import status
from contextlib import asynccontextmanager
from pydantic import BaseModel
import json
import logging
import os
import asyncio
import threading
from datetime import datetime, timedelta
from orders import place_order, get_top_3_futures_from_tv_symbol
from utils import zerodha_login
from dotenv import load_dotenv

# File lock for cache updates
active_contracts_lock = threading.Lock()

# Load environment variables
load_dotenv()

# File paths and secrets from env
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET")
REQUEST_TOKEN_FILE = os.getenv("REQUEST_TOKEN_PATH", "request_token.txt")
ACCESS_TOKEN_FILE = os.getenv("ACCESS_TOKEN_PATH", "access_token.txt")
ACTIVE_CONTRACTS_FILE = os.getenv("ACTIVE_CONTRACTS_PATH", "cache/active_contracts.json")
API_SECRET = os.getenv("KITE_API_SECRET")
API_KEY = os.getenv("KITE_API_KEY")


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
    async def rollover_check():
        import time
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
                # --- Rollover logic starts here ---
                today = datetime.now().date()
                if not os.path.exists(ACTIVE_CONTRACTS_FILE):
                    os.makedirs(os.path.dirname(ACTIVE_CONTRACTS_FILE), exist_ok=True)
                    with open(ACTIVE_CONTRACTS_FILE, "w") as f:
                        json.dump({}, f)
                with active_contracts_lock:
                    with open(ACTIVE_CONTRACTS_FILE, "r") as f:
                        active_map = json.load(f)
                positions = kite.positions()["net"]
                for sym in list(active_map):
                    contracts = get_top_3_futures_from_tv_symbol(sym + "!", kite, 'NFO')
                    if len(contracts) < 2:
                        contracts = get_top_3_futures_from_tv_symbol(sym + "!", kite, 'MCX')
                        if len(contracts) < 2:
                            continue
                    current_contract = contracts[0]
                    next_contract = contracts[1]
                    expiry = current_contract['expiry']
                    if isinstance(expiry, str):
                        expiry = datetime.strptime(expiry, "%Y-%m-%d").date()
                    days_left = (expiry - today).days
                    if 0 < days_left <= 7 and today.weekday() < 5:
                        current_symbol = current_contract['tradingsymbol']
                        next_symbol = next_contract['tradingsymbol']
                        existing_position = next(
                            (p for p in positions if p["tradingsymbol"] == current_symbol and (p["exchange"] == "NFO" or p["exchange"] == "MCX")),
                            None
                        )
                        qty_held = existing_position["quantity"] if existing_position else 0
                        if qty_held > 0:
                            segment = existing_position["exchange"]
                            order_id, error = place_order(kite, current_symbol, "sell", 0, segment, quantity=qty_held)
                            if order_id:
                                order_id, error = place_order(kite, next_symbol, "buy", 0, segment, quantity=qty_held)
                                if order_id:
                                    logging.info(f"✅ Rolled over {sym} from {current_symbol} to {next_symbol}")
                                    active_map[sym.upper()] = next_symbol
                                else:
                                    logging.error(f"❌ Failed to BUY roll over(BUY LEG FAILED) {sym} from {current_symbol} to {next_symbol}")
                            else:
                                logging.error(f"❌ Failed to SELL roll over(SELL LEG FAILED) {sym} from {current_symbol} to {next_symbol}")
                with active_contracts_lock:
                    with open(ACTIVE_CONTRACTS_FILE, "w") as f:
                        json.dump(active_map, f)
                logging.info("Active contracts updated during rollover check.")
            except Exception as e:
                logging.exception("Rollover scheduler failed")
            # Loop will recalculate next 9:25 AM on next iteration

    asyncio.create_task(rollover_check())
    yield

app = FastAPI(lifespan=lifespan)
logging.basicConfig(level=logging.INFO)

from fastapi.responses import PlainTextResponse

@app.get("/logs", response_class=PlainTextResponse)
def get_logs(lines: int = 100):
    """Endpoint to fetch the last N lines of the log file for live monitoring."""
    log_path = "stock_scanner.log"
    if not os.path.exists(log_path):
        return PlainTextResponse("Log file not found.", status_code=404)
    try:
        with open(log_path, "r", encoding="utf-8", errors="ignore") as f:
            all_lines = f.readlines()
        # Return only the last N lines
        output = "".join(all_lines[-lines:])
        return PlainTextResponse(output, status_code=200)
    except Exception as e:
        return PlainTextResponse(f"Error reading log file: {e}", status_code=500)

@app.get("/token", response_class=HTMLResponse)
def token_form() -> HTMLResponse:
    """Render the Zerodha login form."""
    try:
        login_url = kite.login_url()
        return HTMLResponse(content=f"""
        <html>
            <body>
                <p>1. Click the link below to login to Zerodha and get your <b>request_token</b>:</p>
                <a href="{login_url}" target="_blank">{login_url}</a>
                <p>2. Paste the request_token below:</p>
                <form method="post">
                  <input type="text" name="token" size="50"/><br><br>
                  <input type="submit" value="Submit"/>
                </form>
            </body>
        </html>
        """, status_code=200)
    except Exception as e:
        logging.exception("Failed to generate login URL")
        return HTMLResponse(content=f"<p>Error: {str(e)}</p>", status_code=500)

@app.post("/token")
def save_and_refresh_token(token: str = Form(...)) -> JSONResponse:
    """Save and refresh Zerodha access token."""
    try:
        access_token = kite.generate_session(token, api_secret=API_SECRET)["access_token"]
        with open(ACCESS_TOKEN_FILE, "w") as f:
            f.write(access_token)
        logging.info("Access token generated and saved successfully.")
        return JSONResponse(content={"status": "Access token generated"}, status_code=200)
    except Exception as e:
        logging.exception("Failed to save and refresh token")
        return JSONResponse(status_code=500, content={"error": str(e)})


def update_active_contract(active_symbol, tradingsymbol, action, quantity=None):
    """
    Thread-safe update or removal of the active contracts file.
    - On 'buy': add/update the entry.
    - On 'sell': if quantity==0, remove the entry.
    """
    try:
        with active_contracts_lock:
            if not os.path.exists(ACTIVE_CONTRACTS_FILE):
                os.makedirs(os.path.dirname(ACTIVE_CONTRACTS_FILE), exist_ok=True)
                with open(ACTIVE_CONTRACTS_FILE, "w") as f:
                    json.dump({}, f)
            with open(ACTIVE_CONTRACTS_FILE, "r") as f:
                active_map = json.load(f)
            symbol_key = active_symbol.upper()
            if action == "buy":
                active_map[symbol_key] = tradingsymbol
                logging.info(f"Updated active contract for {active_symbol} to {tradingsymbol}")
            with open(ACTIVE_CONTRACTS_FILE, "w") as f:
                json.dump(active_map, f)
    except Exception as e:
        logging.error(f"Failed to update active contracts: {e}")

@app.post("/webhook", response_model=None)
async def webhook(payload: WebhookPayload, token: str = Query(...)) -> JSONResponse:
    """Webhook endpoint for trading signals."""
    if token != WEBHOOK_SECRET:
        logging.warning("Unauthorized access attempt.")
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")
    try:
        logging.info(f"Received Webhook: {payload.json()}")
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
                with active_contracts_lock:
                    if not os.path.exists(ACTIVE_CONTRACTS_FILE):
                        os.makedirs(os.path.dirname(ACTIVE_CONTRACTS_FILE), exist_ok=True)
                        with open(ACTIVE_CONTRACTS_FILE, "w") as f:
                            json.dump({}, f)
                    with open(ACTIVE_CONTRACTS_FILE, "r") as f:
                        active_map = json.load(f)
                tradingsymbol = active_map.get(active_symbol.upper())
                if not tradingsymbol:
                    contracts = get_top_3_futures_from_tv_symbol(tv_symbol, kite, segment)
                    if not contracts:
                        raise HTTPException(status_code=404, detail=f"No futures contracts found for {tv_symbol}")
                    tradingsymbol = contracts[0]['tradingsymbol']
            except Exception as e:
                logging.error(f"Error fetching active contract: {e}")
                raise HTTPException(status_code=500, detail=f"Error fetching active contract: {e}")
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
            else:
                qty_held = 0
        except Exception as e:
            logging.error(f"Error fetching positions/holdings: {e}")
            raise HTTPException(status_code=500, detail=f"Error fetching positions/holdings: {e}")
        if action == "buy":
            if qty_held > 0:
                logging.info(f"Already holding {tradingsymbol}. Skipping buy.")
                return JSONResponse(content={"status": "already holding, buy skipped"}, status_code=200)
            else:
                order_id, error = place_order(kite, tradingsymbol, action, price, segment, quantity)
                if order_id:
                    if(segment == "NFO" or segment == "MCX"):
                        update_active_contract(active_symbol, tradingsymbol, action="buy")
                    return JSONResponse(content={"status": "buy order placed", "order_id": order_id}, status_code=200)
                elif error:
                    return JSONResponse(content={"status": "buy order failed", "error": error}, status_code=500)
        elif action == "sell":
            if qty_held <= 0:
                logging.info(f"No holdings for {tradingsymbol}. Skipping sell.")
                return JSONResponse(content={"status": "no holdings, sell skipped"}, status_code=200)
            else:
                order_id, error = place_order(kite, tradingsymbol, action, price, segment, quantity)
                if order_id:
                    return JSONResponse(content={"status": "sell order placed", "order_id": order_id}, status_code=200)
                elif error:
                    return JSONResponse(content={"status": "sell order failed", "error": error}, status_code=500)

    except HTTPException as he:
        raise he
    except Exception as e:
        logging.exception("Error processing webhook.")
        return JSONResponse(status_code=500, content={"status": "error", "message": str(e)})
