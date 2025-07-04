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
    You can expand this list as needed.
    """
    large_cap_symbols = [
         'RELIANCE',

    ]
   
   
    return large_cap_symbols

def get_historical_data_for_analysis():
    """
    Gets historical data for Nifty50 symbols at 15min interval for last 30 days
    and saves to consolidated CSV.
    """
    # Login to Zerodha
    try:
        kite = zerodha_login()
        logger.info("Successfully logged in to Zerodha")
    except Exception as e:
        logger.error(f"Login failed: {e}")
        return

    # Get instrument list
    instruments = kite.instruments("NSE")
    
    # Get symbols to fetch
    symbols = get_nifty50_symbols()
    
    # Prepare date range
    end_date = datetime.datetime.now()
    start_date = end_date - datetime.timedelta(days=2000)
    
    # Create output directory if not exists
    output_dir = os.path.join(os.path.dirname(__file__), "HistoricalData")
    os.makedirs(output_dir, exist_ok=True)
    
    # Prepare consolidated dataframe
    all_data = []
    
    # Fetch data for each symbol
    for symbol in symbols:
        # Find instrument token
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
            
            # Process and append data
            for candle in data:
                candle['symbol'] = symbol
                all_data.append(candle)
                
            logger.info(f"Fetched {len(data)} candles for {symbol}")
            
        except Exception as e:
            logger.error(f"Failed to fetch data for {symbol}: {e}")
            continue
    
    # Save to CSV
    if all_data:
        df = pd.DataFrame(all_data)
        df['datetime'] = pd.to_datetime(df['date'])
        
        # Reorder columns
        columns = ['datetime', 'symbol', 'open', 'high', 'low', 'close', 'volume']
        df = df[columns]
        
        output_path = os.path.join(output_dir, f"historical_data_{end_date.strftime('%Y%m%d')}.csv")
        df.to_csv(output_path, index=False)
        logger.info(f"Saved historical data to {output_path}")
    else:
        logger.warning("No data fetched to save")
    
    return df if all_data else None

get_historical_data_for_analysis()