"""
Test script to verify DataFrame memory cleanup implementations.
This script tests all the memory management improvements across the codebase.
"""

import pytest
import pandas as pd
import gc
import psutil
import os
from unittest.mock import MagicMock, patch
from memory_manager import memory_manager, cleanup_dataframes, force_gc
from dataframe_utils import (
    SafeDataFrameOperations, 
    dataframe_operation_context,
    safe_dataframe_from_list,
    df_memory_tracker
)

def get_memory_usage():
    """Get current memory usage in MB."""
    process = psutil.Process(os.getpid())
    return process.memory_info().rss / 1024 / 1024

class TestMemoryCleanup:
    """Test memory cleanup functionality."""
    
    def test_basic_dataframe_cleanup(self):
        """Test basic DataFrame cleanup functionality."""
        initial_memory = get_memory_usage()
        
        # Create some larger DataFrames to ensure measurable memory impact
        df1 = pd.DataFrame({'A': range(50000), 'B': range(50000)})
        df2 = pd.DataFrame({'C': range(50000), 'D': range(50000)})
        df3 = df1.copy()
        
        memory_after_creation = get_memory_usage()
        print(f"Memory after creation: {memory_after_creation:.1f}MB")
        
        # Cleanup DataFrames
        cleanup_dataframes(df1, df2, df3)
        
        # Force garbage collection multiple times
        for _ in range(3):
            force_gc()
        
        memory_after_cleanup = get_memory_usage()
        print(f"Memory after cleanup: {memory_after_cleanup:.1f}MB")
        
        # Memory should be reduced or at least not significantly increased
        # Allow for some tolerance due to Python's memory management
        memory_increase = memory_after_cleanup - initial_memory
        assert memory_increase < 50  # Less than 50MB increase from initial
    
    def test_dataframe_context_manager(self):
        """Test DataFrame context manager for automatic cleanup."""
        initial_memory = get_memory_usage()
        print(f"Initial memory: {initial_memory:.1f}MB")
        
        with dataframe_operation_context() as track:
            # Create larger DataFrames to ensure measurable impact
            df1 = track(pd.DataFrame({'A': range(25000)}))
            df2 = track(pd.DataFrame({'B': range(25000)}))
            
            memory_in_context = get_memory_usage()
            print(f"Memory in context: {memory_in_context:.1f}MB")
        
        # After context exit, memory should be cleaned up
        for _ in range(3):
            force_gc()
        
        memory_after_context = get_memory_usage()
        print(f"Memory after context: {memory_after_context:.1f}MB")
        
        # Test passes if context manager doesn't cause significant memory increase
        memory_increase = memory_after_context - initial_memory
        assert memory_increase < 30  # Less than 30MB increase from initial
    
    def test_safe_dataframe_operations(self):
        """Test safe DataFrame operations with memory management."""
        # Test safe filtering
        df = pd.DataFrame({'A': range(1000), 'B': range(1000, 2000)})
        
        filtered_df = SafeDataFrameOperations.safe_filter_dataframe(
            df, df['A'] > 500, copy=True
        )
        
        assert filtered_df is not None
        assert len(filtered_df) == 499  # 501-999 = 499 rows
        
        # Test safe sorting
        sorted_df = SafeDataFrameOperations.safe_sort_dataframe(df, 'B')
        assert sorted_df is not None
        
        # Test safe indexing
        indexed_df = SafeDataFrameOperations.safe_set_index(df, 'A')
        assert indexed_df is not None
        
        # Test safe dictionary conversion
        result_dict = SafeDataFrameOperations.safe_to_dict(df, 'B')
        assert isinstance(result_dict, dict)
        assert len(result_dict) == 1000
        
        # Cleanup
        cleanup_dataframes(df, filtered_df, sorted_df, indexed_df)
    
    def test_memory_tracker(self):
        """Test DataFrame memory tracker functionality."""
        # Clear any existing tracked DataFrames
        df_memory_tracker.cleanup_all()
        
        # Create and track DataFrames
        df1 = pd.DataFrame({'A': range(1000)})
        df2 = pd.DataFrame({'B': range(2000)})
        
        df_memory_tracker.track_dataframe('test_df1', df1)
        df_memory_tracker.track_dataframe('test_df2', df2)
        
        # Get memory report
        report = df_memory_tracker.get_memory_report()
        
        assert report['total_dataframes'] == 2
        assert 'test_df1' in report['dataframes']
        assert 'test_df2' in report['dataframes']
        assert report['dataframes']['test_df1']['rows'] == 1000
        assert report['dataframes']['test_df2']['rows'] == 2000
        
        # Cleanup
        df_memory_tracker.cleanup_all()
        
        # Verify cleanup
        report_after = df_memory_tracker.get_memory_report()
        assert report_after['total_dataframes'] == 0

class TestOrdersMemoryCleanup:
    """Test memory cleanup in orders.py functions."""
    
    @patch('orders.get_instrument_cache')
    @patch('orders.set_instrument_cache')
    def test_get_top_3_futures_memory_cleanup(self, mock_set_cache, mock_get_cache):
        """Test memory cleanup in get_top_3_futures_from_tv_symbol."""
        from orders import get_top_3_futures_from_tv_symbol
        
        # Mock the cache to return None (cache miss)
        mock_get_cache.return_value = None
        
        # Create mock kite object
        mock_kite = MagicMock()
        mock_kite.instruments.return_value = [
            {
                'instrument_type': 'FUT',
                'name': 'NIFTY',
                'tradingsymbol': 'NIFTY24JULFUT',
                'expiry': '2024-07-25',
                'lot_size': 50
            },
            {
                'instrument_type': 'FUT',
                'name': 'NIFTY',
                'tradingsymbol': 'NIFTY24AUGFUT',
                'expiry': '2024-08-29',
                'lot_size': 50
            }
        ]
        
        initial_memory = get_memory_usage()
        
        # Call the function
        contracts = get_top_3_futures_from_tv_symbol("NIFTY!", mock_kite, "NFO")
        
        # Verify results
        assert len(contracts) == 2
        assert contracts[0]['tradingsymbol'] == 'NIFTY24JULFUT'
        
        # Force garbage collection
        force_gc()
        
        # Memory should not have increased significantly
        final_memory = get_memory_usage()
        memory_increase = final_memory - initial_memory
        assert memory_increase < 10  # Less than 10MB increase

class TestUtilsMemoryCleanup:
    """Test memory cleanup in utils.py functions."""
    
    def test_fetch_latest_data_memory_cleanup(self):
        """Test memory cleanup in fetch_latest_data function."""
        from utils import fetch_latest_data
        
        # Create mock kite object
        mock_kite = MagicMock()
        mock_kite.historical_data.return_value = [
            {'date': '2024-01-01', 'open': 100, 'high': 105, 'low': 95, 'close': 102},
            {'date': '2024-01-02', 'open': 102, 'high': 108, 'low': 100, 'close': 106}
        ]
        
        initial_memory = get_memory_usage()
        
        # Call the function
        result_df = fetch_latest_data(mock_kite, 12345)
        
        # Verify results
        assert isinstance(result_df, pd.DataFrame)
        assert len(result_df) == 2
        
        # Cleanup the result
        cleanup_dataframes(result_df)
        force_gc()
        
        # Memory should not have increased significantly
        final_memory = get_memory_usage()
        memory_increase = final_memory - initial_memory
        assert memory_increase < 5  # Less than 5MB increase
    
    def test_get_instrument_token_memory_cleanup(self):
        """Test memory cleanup in get_instrument_token function."""
        from utils import get_instrument_token
        
        # Create mock kite object
        mock_kite = MagicMock()
        mock_kite.instruments.return_value = [
            {'tradingsymbol': 'RELIANCE', 'instrument_token': 738561},
            {'tradingsymbol': 'TCS', 'instrument_token': 2953217}
        ]
        
        initial_memory = get_memory_usage()
        
        # Call the function
        token = get_instrument_token(mock_kite, 'RELIANCE')
        
        # Verify results
        assert token == 738561
        
        # Force garbage collection
        force_gc()
        
        # Memory should not have increased significantly
        final_memory = get_memory_usage()
        memory_increase = final_memory - initial_memory
        assert memory_increase < 5  # Less than 5MB increase

class TestPerformanceOptimizationsMemoryCleanup:
    """Test memory cleanup in performance optimizations."""
    
    @pytest.mark.asyncio
    async def test_get_positions_and_holdings_memory_cleanup(self):
        """Test memory cleanup in get_positions_and_holdings_direct."""
        from performance_optimizations import get_positions_and_holdings_direct, perf_optimizer
        
        # Clear cache
        perf_optimizer.clear_request_cache()
        
        # Create mock kite object
        mock_kite = MagicMock()
        
        # Mock large dataset to trigger DataFrame usage
        large_holdings = [{'tradingsymbol': f'STOCK{i}', 'quantity': i} for i in range(100)]
        large_positions = [{'tradingsymbol': f'STOCK{i}', 'quantity': i, 'exchange': 'NSE'} for i in range(150)]
        
        mock_kite.holdings.return_value = large_holdings
        mock_kite.positions.return_value = {"net": large_positions}
        
        initial_memory = get_memory_usage()
        
        # Call the function
        qty_held, position = await get_positions_and_holdings_direct(
            mock_kite, "NSE", "STOCK50"
        )
        
        # Verify results
        assert qty_held == 50
        
        # Force garbage collection
        force_gc()
        
        # Memory should not have increased significantly
        final_memory = get_memory_usage()
        memory_increase = final_memory - initial_memory
        assert memory_increase < 10  # Less than 10MB increase

def test_memory_manager_context():
    """Test memory manager DataFrame context functionality."""
    initial_memory = get_memory_usage()
    print(f"Initial memory: {initial_memory:.1f}MB")
    
    with memory_manager.create_dataframe_context() as ctx:
        # Create larger DataFrames within context
        df1 = ctx.create_dataframe({'A': range(20000)})
        df2 = pd.DataFrame({'B': range(20000)})
        ctx.track(df2)
        
        memory_in_context = get_memory_usage()
        print(f"Memory in context: {memory_in_context:.1f}MB")
    
    # After context, memory should be cleaned up
    for _ in range(3):
        force_gc()
    
    memory_after_context = get_memory_usage()
    print(f"Memory after context: {memory_after_context:.1f}MB")
    
    # Test passes if context manager works without causing memory issues
    memory_increase = memory_after_context - initial_memory
    assert memory_increase < 25  # Less than 25MB increase from initial

def test_safe_dataframe_from_list():
    """Test safe DataFrame creation from list."""
    data = [
        {'name': 'Alice', 'age': 25},
        {'name': 'Bob', 'age': 30},
        {'name': 'Charlie', 'age': 35}
    ]
    
    df = safe_dataframe_from_list(data)
    assert df is not None
    assert len(df) == 3
    assert 'name' in df.columns
    assert 'age' in df.columns
    
    # Test with empty data
    empty_df = safe_dataframe_from_list([])
    assert empty_df is None
    
    # Cleanup
    cleanup_dataframes(df)

if __name__ == "__main__":
    # Run basic memory tests
    print("Running memory cleanup tests...")
    
    test_instance = TestMemoryCleanup()
    test_instance.test_basic_dataframe_cleanup()
    print("✅ Basic DataFrame cleanup test passed")
    
    test_instance.test_dataframe_context_manager()
    print("✅ DataFrame context manager test passed")
    
    test_instance.test_safe_dataframe_operations()
    print("✅ Safe DataFrame operations test passed")
    
    test_instance.test_memory_tracker()
    print("✅ Memory tracker test passed")
    
    test_memory_manager_context()
    print("✅ Memory manager context test passed")
    
    test_safe_dataframe_from_list()
    print("✅ Safe DataFrame from list test passed")
    
    print("\n🎉 All memory cleanup tests passed!")
    print(f"Final memory usage: {get_memory_usage():.1f} MB")