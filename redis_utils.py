import redis
import json
import pandas as pd
import logging
import io
import time
from typing import Optional

logger = logging.getLogger(__name__)

# Create a Redis connection pool to prevent connection leaks
# decode_responses=True makes redis-py return python strings instead of bytes.
try:
    redis_pool = redis.ConnectionPool(
        host='localhost', 
        port=6379, 
        db=0, 
        decode_responses=True,
        max_connections=20,  # Limit max connections
        retry_on_timeout=True,
        socket_keepalive=True,
        socket_keepalive_options={}
    )
    redis_client = redis.Redis(connection_pool=redis_pool)
    
    # Test connection immediately
    redis_client.ping()
    logger.info("Connected to Redis server successfully with connection pooling.")
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

def is_duplicate(symbol, timestamp, max_retries: int = 3):
    """Checks for duplicate webhook calls using a Redis key with expiry."""
    key = f"idempotency:{symbol}:{timestamp}"
    
    for attempt in range(max_retries):
        try:
            # Try to set the key with a 1-day expiry, only if it doesn't exist.
            # set returns True if the key was set, False otherwise.
            was_set = redis_client.set(key, "1", nx=True, ex=86400)
            return not was_set  # True if duplicate (key already existed), False if new.
        except redis.ConnectionError as e:
            if attempt < max_retries - 1:
                logger.warning(f"Redis connection error on attempt {attempt + 1}, retrying: {e}")
                time.sleep(0.1 * (2 ** attempt))  # Exponential backoff
                continue
            else:
                logger.error(f"Redis connection failed after {max_retries} attempts: {e}")
                # Fail safe: assume not duplicate to avoid blocking trades
                return False
        except Exception as e:
            logger.error(f"Unexpected Redis error in is_duplicate: {e}")
            return False

def set_instrument_cache(segment: str, instruments_df: pd.DataFrame, max_retries: int = 3):
    """Serializes a DataFrame to JSON and stores it in Redis for 24 hours."""
    key = f"instrument_cache:{segment}"
    
    for attempt in range(max_retries):
        try:
            # Using 'split' orient is efficient for pandas DataFrames.
            json_data = instruments_df.to_json(orient="split")
            redis_client.set(key, json_data, ex=86400) # 24-hour expiry
            logger.info(f"Successfully cached instruments for segment {segment}.")
            return
        except redis.ConnectionError as e:
            if attempt < max_retries - 1:
                logger.warning(f"Redis connection error caching {segment}, attempt {attempt + 1}, retrying: {e}")
                time.sleep(0.1 * (2 ** attempt))  # Exponential backoff
                continue
            else:
                logger.error(f"Failed to cache {segment} after {max_retries} attempts: {e}")
        except Exception as e:
            logger.error(f"Failed to set instrument cache for {segment} in Redis: {e}")
            break


def get_instrument_cache(segment: str, max_retries: int = 3) -> Optional[pd.DataFrame]:
    """Retrieves and deserializes a DataFrame from Redis."""
    key = f"instrument_cache:{segment}"
    
    for attempt in range(max_retries):
        try:
            json_data = redis_client.get(key)
            if json_data:
                # Wrap json_data in StringIO to avoid FutureWarning
                df = pd.read_json(io.StringIO(json_data), orient="split")
                logger.info(f"Successfully retrieved instrument cache for {segment} from Redis.")
                return df
            logger.warning(f"No instrument cache found in Redis for segment {segment}.")
            return None
        except redis.ConnectionError as e:
            if attempt < max_retries - 1:
                logger.warning(f"Redis connection error retrieving {segment}, attempt {attempt + 1}, retrying: {e}")
                time.sleep(0.1 * (2 ** attempt))  # Exponential backoff
                continue
            else:
                logger.error(f"Failed to retrieve {segment} cache after {max_retries} attempts: {e}")
                return None
        except Exception as e:
            logger.error(f"Failed to get instrument cache for {segment} from Redis: {e}")
            return None 