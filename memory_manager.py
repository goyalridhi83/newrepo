"""
Memory management utilities for the trading application.
Provides garbage collection, memory monitoring, and cleanup functions.
"""

import gc
import logging
import psutil
import os
import asyncio
from typing import Optional
import pandas as pd

logger = logging.getLogger(__name__)

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
        """Explicitly cleanup pandas DataFrames."""
        for df in dataframes:
            if isinstance(df, pd.DataFrame):
                try:
                    del df
                except:
                    pass
        self.force_garbage_collection()
    
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