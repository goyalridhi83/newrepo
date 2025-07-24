"""
Memory management utilities for the trading application.
Provides garbage collection, memory monitoring, and cleanup functions.
"""

import gc
import logging
import psutil
import os
import asyncio
from typing import Optional, List, Any
import pandas as pd
from contextlib import contextmanager

logger = logging.getLogger(__name__)

class DataFrameContext:
    """Context manager for automatic DataFrame cleanup."""
    
    def __init__(self, memory_manager):
        self.memory_manager = memory_manager
        self.dataframes: List[pd.DataFrame] = []
        
    def __enter__(self):
        return self
        
    def __exit__(self, exc_type, exc_val, exc_tb):
        """Cleanup all tracked DataFrames on exit."""
        if self.dataframes:
            self.memory_manager.cleanup_dataframes(*self.dataframes)
            self.dataframes.clear()
    
    def track(self, *dataframes):
        """Track DataFrames for automatic cleanup."""
        for df in dataframes:
            if isinstance(df, pd.DataFrame):
                self.dataframes.append(df)
        return dataframes[0] if len(dataframes) == 1 else dataframes
    
    def create_dataframe(self, *args, **kwargs) -> pd.DataFrame:
        """Create and track a new DataFrame."""
        df = pd.DataFrame(*args, **kwargs)
        self.dataframes.append(df)
        return df

class MemoryManager:
    """Centralized memory management for the trading application."""
    
    def __init__(self, memory_threshold_mb: int = 500):
        self.memory_threshold_mb = memory_threshold_mb
        self.process = psutil.Process(os.getpid())
        
    def get_memory_usage(self) -> float:
        """Get current memory usage in MB."""
        return self.process.memory_info().rss / 1024 / 1024
    
    def force_garbage_collection(self) -> int:
        """Force garbage collection and return number of objects collected."""
        before = len(gc.get_objects())
        collected = gc.collect()
        after = len(gc.get_objects())
        
        logger.info(f"Garbage collection: {collected} cycles, {before - after} objects freed")
        return collected
    
    def check_memory_threshold(self) -> bool:
        """Check if memory usage exceeds threshold."""
        current_memory = self.get_memory_usage()
        if current_memory > self.memory_threshold_mb:
            logger.warning(f"Memory usage high: {current_memory:.1f}MB (threshold: {self.memory_threshold_mb}MB)")
            return True
        return False
    
    def cleanup_dataframes(self, *dataframes) -> None:
        """Explicitly cleanup pandas DataFrames with comprehensive memory management."""
        cleaned_count = 0
        for df in dataframes:
            if isinstance(df, pd.DataFrame):
                try:
                    # Clear DataFrame data explicitly
                    if hasattr(df, '_mgr'):
                        df._mgr = None
                    if hasattr(df, '_item_cache'):
                        df._item_cache.clear()
                    
                    # Delete the DataFrame
                    del df
                    cleaned_count += 1
                except Exception as e:
                    logger.debug(f"Error cleaning DataFrame: {e}")
                    pass
        
        if cleaned_count > 0:
            logger.debug(f"Cleaned up {cleaned_count} DataFrames")
            self.force_garbage_collection()
    
    def safe_dataframe_operation(self, operation_func, *args, **kwargs):
        """
        Safely execute DataFrame operations with automatic cleanup.
        
        Args:
            operation_func: Function that returns a DataFrame or tuple of DataFrames
            *args, **kwargs: Arguments to pass to the operation function
            
        Returns:
            Result of the operation function
        """
        temp_dataframes = []
        try:
            result = operation_func(*args, **kwargs)
            
            # If result contains DataFrames, track them for cleanup
            if isinstance(result, pd.DataFrame):
                temp_dataframes.append(result)
            elif isinstance(result, (list, tuple)):
                for item in result:
                    if isinstance(item, pd.DataFrame):
                        temp_dataframes.append(item)
            
            return result
        except Exception as e:
            logger.error(f"Error in DataFrame operation: {e}")
            raise
        finally:
            # Note: We don't cleanup here as the caller might need the DataFrames
            # This method is for tracking purposes
            pass
    
    def create_dataframe_context(self):
        """Create a context manager for DataFrame operations."""
        return DataFrameContext(self)
    
    async def monitor_memory(self, interval_seconds: int = 300) -> None:
        """Background task to monitor memory usage."""
        while True:
            try:
                current_memory = self.get_memory_usage()
                
                if self.check_memory_threshold():
                    self.force_garbage_collection()
                    new_memory = self.get_memory_usage()
                    logger.info(f"Memory after cleanup: {new_memory:.1f}MB (freed: {current_memory - new_memory:.1f}MB)")
                
                await asyncio.sleep(interval_seconds)
                
            except Exception as e:
                logger.error(f"Memory monitoring error: {e}")
                await asyncio.sleep(60)  # Wait before retrying

# Global memory manager instance
memory_manager = MemoryManager()

def cleanup_dataframes(*dataframes):
    """Convenience function to cleanup DataFrames."""
    memory_manager.cleanup_dataframes(*dataframes)

def force_gc():
    """Convenience function to force garbage collection."""
    return memory_manager.force_garbage_collection()

def get_memory_usage():
    """Convenience function to get memory usage."""
    return memory_manager.get_memory_usage()