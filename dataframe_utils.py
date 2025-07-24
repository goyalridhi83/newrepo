"""
DataFrame utilities with comprehensive memory management.
This module provides safe DataFrame operations with automatic cleanup.
"""

import pandas as pd
import logging
from typing import List, Optional, Dict, Any, Callable, Union
from contextlib import contextmanager
from memory_manager import memory_manager, cleanup_dataframes, force_gc

logger = logging.getLogger(__name__)

class SafeDataFrameOperations:
    """Safe DataFrame operations with automatic memory management."""
    
    @staticmethod
    def safe_read_json(json_data: str, **kwargs) -> Optional[pd.DataFrame]:
        """Safely read JSON data into DataFrame with error handling."""
        try:
            import io
            df = pd.read_json(io.StringIO(json_data), **kwargs)
            logger.debug(f"Successfully created DataFrame with {len(df)} rows")
            return df
        except Exception as e:
            logger.error(f"Error reading JSON to DataFrame: {e}")
            return None
    
    @staticmethod
    def safe_filter_dataframe(df: pd.DataFrame, condition: Any, copy: bool = True) -> Optional[pd.DataFrame]:
        """Safely filter DataFrame with automatic cleanup of intermediate results."""
        if df is None or df.empty:
            return None
            
        try:
            filtered_df = df[condition]
            if copy:
                result = filtered_df.copy()
                # Clean up the filtered view
                cleanup_dataframes(filtered_df)
                return result
            return filtered_df
        except Exception as e:
            logger.error(f"Error filtering DataFrame: {e}")
            return None
    
    @staticmethod
    def safe_sort_dataframe(df: pd.DataFrame, by: Union[str, List[str]], **kwargs) -> Optional[pd.DataFrame]:
        """Safely sort DataFrame with memory management."""
        if df is None or df.empty:
            return None
            
        try:
            sorted_df = df.sort_values(by, **kwargs)
            return sorted_df
        except Exception as e:
            logger.error(f"Error sorting DataFrame: {e}")
            return None
    
    @staticmethod
    def safe_set_index(df: pd.DataFrame, keys: Union[str, List[str]], **kwargs) -> Optional[pd.DataFrame]:
        """Safely set DataFrame index with memory management."""
        if df is None or df.empty:
            return None
            
        try:
            indexed_df = df.set_index(keys, **kwargs)
            return indexed_df
        except Exception as e:
            logger.error(f"Error setting DataFrame index: {e}")
            return None
    
    @staticmethod
    def safe_to_dict(df: pd.DataFrame, column: str) -> Dict[Any, Any]:
        """Safely convert DataFrame column to dictionary."""
        if df is None or df.empty:
            return {}
            
        try:
            if column in df.columns:
                return df[column].to_dict()
            else:
                logger.warning(f"Column '{column}' not found in DataFrame")
                return {}
        except Exception as e:
            logger.error(f"Error converting DataFrame to dict: {e}")
            return {}

@contextmanager
def dataframe_operation_context():
    """Context manager for DataFrame operations with automatic cleanup."""
    dataframes = []
    
    def track_dataframe(df: pd.DataFrame) -> pd.DataFrame:
        """Track a DataFrame for automatic cleanup."""
        if isinstance(df, pd.DataFrame):
            dataframes.append(df)
        return df
    
    try:
        yield track_dataframe
    finally:
        # Cleanup all tracked DataFrames
        if dataframes:
            cleanup_dataframes(*dataframes)
            dataframes.clear()
        force_gc()

def safe_dataframe_from_list(data: List[Dict], **kwargs) -> Optional[pd.DataFrame]:
    """Safely create DataFrame from list of dictionaries."""
    if not data:
        return None
        
    try:
        df = pd.DataFrame(data, **kwargs)
        logger.debug(f"Created DataFrame from list with {len(df)} rows")
        return df
    except Exception as e:
        logger.error(f"Error creating DataFrame from list: {e}")
        return None

def safe_dataframe_merge(left: pd.DataFrame, right: pd.DataFrame, **kwargs) -> Optional[pd.DataFrame]:
    """Safely merge DataFrames with memory management."""
    if left is None or right is None:
        return None
        
    try:
        merged_df = pd.merge(left, right, **kwargs)
        logger.debug(f"Merged DataFrames: {len(left)} + {len(right)} -> {len(merged_df)} rows")
        return merged_df
    except Exception as e:
        logger.error(f"Error merging DataFrames: {e}")
        return None

def batch_process_dataframe(df: pd.DataFrame, batch_size: int = 1000, 
                          process_func: Callable = None) -> List[Any]:
    """Process DataFrame in batches to manage memory usage."""
    if df is None or df.empty or process_func is None:
        return []
    
    results = []
    total_rows = len(df)
    
    try:
        for start_idx in range(0, total_rows, batch_size):
            end_idx = min(start_idx + batch_size, total_rows)
            batch_df = df.iloc[start_idx:end_idx].copy()
            
            try:
                batch_result = process_func(batch_df)
                if batch_result is not None:
                    results.append(batch_result)
            except Exception as e:
                logger.error(f"Error processing batch {start_idx}-{end_idx}: {e}")
            finally:
                # Always cleanup the batch DataFrame
                cleanup_dataframes(batch_df)
        
        logger.info(f"Processed {total_rows} rows in {len(range(0, total_rows, batch_size))} batches")
        return results
        
    except Exception as e:
        logger.error(f"Error in batch processing: {e}")
        return results

class DataFrameMemoryTracker:
    """Track DataFrame memory usage and provide cleanup recommendations."""
    
    def __init__(self):
        self.tracked_dataframes = {}
        self.memory_threshold_mb = 100  # Alert if DataFrames use more than 100MB
    
    def track_dataframe(self, name: str, df: pd.DataFrame):
        """Track a DataFrame's memory usage."""
        if isinstance(df, pd.DataFrame):
            memory_usage = df.memory_usage(deep=True).sum() / 1024 / 1024  # MB
            self.tracked_dataframes[name] = {
                'dataframe': df,
                'memory_mb': memory_usage,
                'rows': len(df),
                'columns': len(df.columns)
            }
            
            if memory_usage > self.memory_threshold_mb:
                logger.warning(f"Large DataFrame '{name}': {memory_usage:.1f}MB, {len(df)} rows")
    
    def get_memory_report(self) -> Dict[str, Any]:
        """Get memory usage report for all tracked DataFrames."""
        total_memory = sum(info['memory_mb'] for info in self.tracked_dataframes.values())
        
        return {
            'total_dataframes': len(self.tracked_dataframes),
            'total_memory_mb': round(total_memory, 2),
            'dataframes': {
                name: {
                    'memory_mb': round(info['memory_mb'], 2),
                    'rows': info['rows'],
                    'columns': info['columns']
                }
                for name, info in self.tracked_dataframes.items()
            }
        }
    
    def cleanup_large_dataframes(self, threshold_mb: float = 50):
        """Cleanup DataFrames larger than threshold."""
        to_remove = []
        for name, info in self.tracked_dataframes.items():
            if info['memory_mb'] > threshold_mb:
                cleanup_dataframes(info['dataframe'])
                to_remove.append(name)
                logger.info(f"Cleaned up large DataFrame '{name}': {info['memory_mb']:.1f}MB")
        
        for name in to_remove:
            del self.tracked_dataframes[name]
    
    def cleanup_all(self):
        """Cleanup all tracked DataFrames."""
        dataframes = [info['dataframe'] for info in self.tracked_dataframes.values()]
        if dataframes:
            cleanup_dataframes(*dataframes)
        self.tracked_dataframes.clear()
        logger.info("Cleaned up all tracked DataFrames")

# Global DataFrame memory tracker
df_memory_tracker = DataFrameMemoryTracker()

# Convenience functions
def track_dataframe(name: str, df: pd.DataFrame) -> pd.DataFrame:
    """Track a DataFrame for memory monitoring."""
    df_memory_tracker.track_dataframe(name, df)
    return df

def get_dataframe_memory_report() -> Dict[str, Any]:
    """Get memory usage report for all DataFrames."""
    return df_memory_tracker.get_memory_report()

def cleanup_large_dataframes(threshold_mb: float = 50):
    """Cleanup large DataFrames."""
    df_memory_tracker.cleanup_large_dataframes(threshold_mb)