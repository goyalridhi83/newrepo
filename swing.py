import pandas as pd
import numpy as np
from datetime import datetime
from kiteconnect import KiteConnect
from utils import zerodha_login  # Your existing login function

# Configurable Parameters
SWING_PERIOD = 3
START_DATE = "2025-04-01"
END_DATE = "2025-07-01"
INSTRUMENT = "ICICIBANK"
TIMEFRAME = "60minute"  # 1-hour candles
LOT_SIZE = 550  # 1 Lot

def fetch_historical_data(kite, instrument, interval, start_date, end_date):
    instrument_token = kite.ltp(f'NSE:{instrument}')[f'NSE:{instrument}']['instrument_token']
    from_date = datetime.strptime(start_date, '%Y-%m-%d')
    to_date = datetime.strptime(end_date, '%Y-%m-%d')
    data = kite.historical_data(instrument_token, from_date, to_date, interval)
    df = pd.DataFrame(data)
    df['date'] = pd.to_datetime(df['date'])
    return df

def calculate_tsl(df):
    df['res'] = df['high'].rolling(SWING_PERIOD).max()
    df['sup'] = df['low'].rolling(SWING_PERIOD).min()

    df['avd'] = np.where(df['close'] > df['res'].shift(1), 1,
                  np.where(df['close'] < df['sup'].shift(1), -1, 0))

    df['avn'] = df['avd'].replace(0, np.nan).ffill()
    df['tsl'] = np.where(df['avn'] == 1, df['sup'], df['res'])
    return df

def backtest_strategy(df):
    df = calculate_tsl(df)

    position = 0
    entry_price = 0
    trades = []

    for i in range(1, len(df)):
        # BUY Condition: Crossover close > TSL
        if position == 0 and df['close'].iloc[i-1] <= df['tsl'].iloc[i-1] and df['close'].iloc[i] > df['tsl'].iloc[i]:
            position = 1
            entry_price = df['close'].iloc[i]
            entry_time = df['date'].iloc[i]

        # SELL Condition: Crossunder close < TSL AND current price >= entry price
        elif position == 1 and df['close'].iloc[i-1] >= df['tsl'].iloc[i-1] and df['close'].iloc[i] < df['tsl'].iloc[i]:
            current_price = df['close'].iloc[i]
            if current_price >= entry_price:
                exit_price = current_price
                exit_time = df['date'].iloc[i]
                profit = (exit_price - entry_price) * LOT_SIZE
                trades.append({
                    "Entry Time": entry_time,
                    "Exit Time": exit_time,
                    "Entry Price": entry_price,
                    "Exit Price": exit_price,
                    "Profit": profit
                })
                position = 0  # Reset position

    return pd.DataFrame(trades)

def main():
    try:
        kite = zerodha_login()
    except Exception as e:
        print(f"Zerodha Login Failed: {e}")
        return

    df = fetch_historical_data(kite, INSTRUMENT, TIMEFRAME, START_DATE, END_DATE)
    trades_df = backtest_strategy(df)
    print(trades_df)
    print(f"Total Profit: {trades_df['Profit'].sum():.2f}")

if __name__ == "__main__":
    main()
