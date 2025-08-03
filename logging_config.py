"""
Centralized logging configuration to prevent duplicate handlers and memory leaks.
"""

import logging
import logging.handlers
import os
import pytz
from datetime import datetime
from typing import Optional

class ISTFormatter(logging.Formatter):
    """Custom formatter that converts timestamps to IST timezone."""
    
    def __init__(self, fmt=None, datefmt=None):
        super().__init__(fmt, datefmt)
        self.ist_tz = pytz.timezone('Asia/Kolkata')
    
    def converter(self, timestamp):
        """Convert timestamp to IST."""
        return datetime.fromtimestamp(timestamp, self.ist_tz)
    
    def formatTime(self, record, datefmt=None):
        """Format time in IST."""
        dt = self.converter(record.created)
        if datefmt:
            return dt.strftime(datefmt)
        else:
            return dt.strftime("%Y-%m-%d %H:%M:%S %Z")

class LoggingManager:
    """Centralized logging manager to prevent duplicate handlers."""
    
    def __init__(self):
        self._configured = False
        self._handlers = []
    
    def setup_logging(
        self, 
        log_file: str = "stock_scanner.log",
        max_bytes: int = 50 * 1024 * 1024,  # 50MB
        backup_count: int = 5,
        log_level: int = logging.INFO
    ):
        """Setup centralized logging configuration."""
        
        if self._configured:
            return  # Already configured
        
        # Clear any existing handlers to prevent duplicates
        root_logger = logging.getLogger()
        for handler in root_logger.handlers[:]:
            root_logger.removeHandler(handler)
            handler.close()
        
        # Create formatter
        formatter = ISTFormatter(
            '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
        )
        
        # File handler with rotation
        try:
            file_handler = logging.handlers.RotatingFileHandler(
                log_file,
                maxBytes=max_bytes,
                backupCount=backup_count,
                encoding='utf-8'
            )
            file_handler.setFormatter(formatter)
            file_handler.setLevel(log_level)
            self._handlers.append(file_handler)
            
            # Console handler
            console_handler = logging.StreamHandler()
            console_handler.setFormatter(formatter)
            console_handler.setLevel(log_level)
            self._handlers.append(console_handler)
            
            # Configure root logger
            root_logger.setLevel(log_level)
            for handler in self._handlers:
                root_logger.addHandler(handler)
            
            self._configured = True
            logging.info("Logging configuration completed successfully")
            
        except Exception as e:
            print(f"Failed to setup logging: {e}")
            raise
    
    def cleanup(self):
        """Cleanup logging handlers."""
        for handler in self._handlers:
            try:
                handler.close()
            except:
                pass
        self._handlers.clear()
        self._configured = False

# Global logging manager
logging_manager = LoggingManager()

def setup_logging(**kwargs):
    """Convenience function to setup logging."""
    logging_manager.setup_logging(**kwargs)

def cleanup_logging():
    """Convenience function to cleanup logging."""
    logging_manager.cleanup()