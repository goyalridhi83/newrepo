import pandas as pd
import os, sys
import datetime
import logging
import time

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

from utils import zerodha_login

def get_nifty50_symbols():
    """
    Returns a list of NSE symbols to monitor.
    """
    large_cap_symbols = [
         'RELIANCE',
         # Add more symbols as needed
    ]
    return large_cap_symbols


def get_historical_data_for_analysis():
    """
    Gets historical data for symbols and saves to consolidated CSV.
    """
    try:
        kite = zerodha_login()
        logger.info("Successfully logged in to Zerodha")
    except Exception as e:
        logger.error(f"Login failed: {e}")
        return

    instruments = kite.instruments("NSE")
    symbols = get_nifty50_symbols()
    end_date = datetime.datetime.now()
    start_date = end_date - datetime.timedelta(days=2000)

    output_dir = os.path.join(os.path.dirname(__file__), "HistoricalData")
    os.makedirs(output_dir, exist_ok=True)

    all_data = []
    for symbol in symbols:
        instrument = next((i for i in instruments if i['tradingsymbol'] == symbol), None)
        if not instrument:
            logger.warning(f"Instrument not found for symbol: {symbol}")
            continue
        try:
            logger.info(f"Fetching data for {symbol}...")
            data = kite.historical_data(
                instrument_token=instrument['instrument_token'],
                from_date=start_date,
                to_date=end_date,
                interval="day",
                continuous=False,
                oi=True
            )
            for candle in data:
                candle['symbol'] = symbol
                all_data.append(candle)
            logger.info(f"Fetched {len(data)} candles for {symbol}")
        except Exception as e:
            logger.error(f"Failed to fetch data for {symbol}: {e}")

    if all_data:
        df = pd.DataFrame(all_data)
        df['datetime'] = pd.to_datetime(df['date'])
        columns = ['datetime', 'symbol', 'open', 'high', 'low', 'close', 'volume']
        df = df[columns]
        output_path = os.path.join(output_dir, f"historical_data_{end_date.strftime('%Y%m%d')}.csv")
        df.to_csv(output_path, index=False)
        logger.info(f"Saved historical data to {output_path}")
        return df
    else:
        logger.warning("No data fetched to save")
        return None


# ==============================================
# Lot Tracking Logic Example (Placeholder)
# ==============================================

class LotManager:
    """
    Manages multiple lots for a symbol with independent entry/exit tracking.
    """
    def __init__(self, max_lots=2):
        self.max_lots = max_lots
        self.lots = []  # Each lot is a dict with 'entry_price' and 'active' status

    def try_enter_lot(self, price):
        """
        Attempt to enter a new lot on a buy signal.
        """
        if len(self.lots) < self.max_lots:
            self.lots.append({'entry_price': price, 'active': True})
            logger.info(f"Entered new lot at {price}. Total active lots: {len(self.lots)}")
        else:
            logger.info(f"Max {self.max_lots} lots already open. No new lot added.")

    def try_exit_lots(self, price, tsl_price):
        """
        Attempt to exit active lots based on sell condition and no-loss logic.
        """
        exited = 0
        for lot in self.lots:
            if lot['active'] and price <= tsl_price and price >= lot['entry_price']:
                lot['active'] = False
                exited += 1
        if exited > 0:
            logger.info(f"Exited {exited} lot(s) at {price}. Remaining active lots: {self.active_lot_count()}")

    def active_lot_count(self):
        return sum(1 for lot in self.lots if lot['active'])


# ==============================================
# Example Usage:
# ==============================================

if __name__ == "__main__":
    df = get_historical_data_for_analysis()

    if df is not None:
        lot_manager = LotManager(max_lots=2)

        # Example: Run your TSL logic for 'RELIANCE'
        reliance_data = df[df['symbol'] == 'RELIANCE'].sort_values('datetime')

        for idx, row in reliance_data.iterrows():
            price = row['close']

            # Placeholder: Your actual buy/sell condition logic here
            buy_signal = False  # Replace with actual TSL logic
            sell_signal = False  # Replace with actual TSL logic
            tsl_price = 0  # Replace with calculated TSL

            # Example for demo purposes only:
            if price % 50 == 0:  # Dummy buy signal when price is multiple of 50
                buy_signal = True
            if price % 70 == 0:  # Dummy sell signal when price is multiple of 70
                sell_signal = True
                tsl_price = price * 0.98  # Example TSL as 2% below current price

            if buy_signal:
                lot_manager.try_enter_lot(price)

            if sell_signal:
                lot_manager.try_exit_lots(price, tsl_price)

