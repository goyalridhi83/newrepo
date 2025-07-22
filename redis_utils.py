import redis
import json
import pandas as pd
import logging
import io
from typing import Optional

logger = logging.getLogger(__name__)

# Create a Redis client (adjust host/port if needed)
# decode_responses=True makes redis-py return python strings instead of bytes.
try:
    redis_client = redis.Redis(host='localhost', port=6379, db=0, decode_responses=True)
    # Test connection immediately
    redis_client.ping()
    logger.info("Connected to Redis server successfully.")
except Exception as e:
    logger.critical(f"Failed to connect to Redis server: {e}")
    raise RuntimeError(f"Failed to connect to Redis server: {e}")

def check_redis_connection():
    """Call this at app startup to ensure Redis is available."""
    try:
        redis_client.ping()
        logger.info("Redis connection check passed.")
        return True
    except Exception as e:
        logger.critical(f"Redis connection check failed: {e}")
        return False

def is_duplicate(symbol, timestamp):
    """Checks for duplicate webhook calls using a Redis key with expiry."""
    key = f"idempotency:{symbol}:{timestamp}"
    # Try to set the key with a 1-day expiry, only if it doesn't exist.
    # set returns True if the key was set, False otherwise.
    was_set = redis_client.set(key, "1", nx=True, ex=86400)
    return not was_set  # True if duplicate (key already existed), False if new.

def set_instrument_cache(segment: str, instruments_df: pd.DataFrame):
    """Serializes a DataFrame to JSON and stores it in Redis for 24 hours."""
    key = f"instrument_cache:{segment}"
    try:
        # Using 'split' orient is efficient for pandas DataFrames.
        json_data = instruments_df.to_json(orient="split")
        redis_client.set(key, json_data, ex=86400) # 24-hour expiry
        logger.info(f"Successfully cached instruments for segment {segment}.")
    except Exception as e:
        logger.error(f"Failed to set instrument cache for {segment} in Redis: {e}")


def get_instrument_cache(segment: str) -> Optional[pd.DataFrame]:
    """Retrieves and deserializes a DataFrame from Redis."""
    key = f"instrument_cache:{segment}"
    try:
        json_data = redis_client.get(key)
        if json_data:
            # Wrap json_data in StringIO to avoid FutureWarning
            df = pd.read_json(io.StringIO(json_data), orient="split")
            logger.info(f"Successfully retrieved instrument cache for {segment} from Redis.")
            return df
        logger.warning(f"No instrument cache found in Redis for segment {segment}.")
        return None
    except Exception as e:
        logger.error(f"Failed to get instrument cache for {segment} from Redis: {e}")
        return None 