from flask import Flask, request, jsonify
import json
import logging
from orders import place_order, get_top_3_futures_from_tv_symbol
import config
from utils import zerodha_login

app = Flask(__name__)
logging.basicConfig(level=logging.INFO)

# Initialize Zerodha Connection
kite = zerodha_login()

@app.route('/webhook', methods=['POST'])
def webhook():
    try:
        # Validate Secret Token
        token = request.args.get('token')
        if token != config.WEBHOOK_SECRET:
            logging.warning("Unauthorized access attempt.")
            return jsonify({"status": "unauthorized"}), 401

        data = request.get_json()
        logging.info(f"Received Webhook: {json.dumps(data)}")

        # Extract required fields
        action = data.get("action")
        tv_symbol = data.get("symbol")
        segment = data.get("segment", "NSE")  # Default to NSE if not provided
        price = float(data.get("price", 0))
        time_received = data.get("time")

        if action not in ["buy", "sell"]:
            logging.error(f"Invalid action received: {action}")
            return jsonify({"status": "invalid action"}), 400

        # Get instrument symbol based on segment
        if segment == "NFO":
            symbol_info = get_top_3_futures_from_tv_symbol(tv_symbol)[0]
            tradingsymbol = symbol_info['tradingsymbol']
        else:
            tradingsymbol = tv_symbol  # For NSE equity, TradingView symbol works as-is

        # Fetch current positions
        positions = kite.positions()["net"]
        existing_position = next(
            (p for p in positions if p["tradingsymbol"] == tradingsymbol and p["exchange"] == segment),
            None
        )

        qty_held = existing_position["quantity"] if existing_position else 0

        # Handle Buy
        if action == "buy":
            if qty_held > 0:
                logging.info(f"Already holding position for {tradingsymbol}. Skipping buy order.")
                return jsonify({"status": "already holding position, buy skipped"}), 200
            else:
                order_id = place_order(tradingsymbol, action, price, segment)
                return jsonify({"status": "buy order placed", "order_id": order_id}), 200

        # Handle Sell
        elif action == "sell":
            if qty_held <= 0:
                logging.info(f"No holdings for {tradingsymbol}. Skipping sell order to avoid short selling.")
                return jsonify({"status": "no holdings, sell skipped"}), 200
            else:
                order_id = place_order(tradingsymbol, action, price, segment)
                return jsonify({"status": "sell order placed", "order_id": order_id}), 200

    except Exception as e:
        logging.exception("Error processing webhook.")
        return jsonify({"status": "error", "message": str(e)}), 500

if __name__ == '__main__':
    app.run(host="0.0.0.0", port=5000)
