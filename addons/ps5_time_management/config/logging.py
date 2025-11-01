"""Logging configuration for PS5 Time Management add-on"""
import logging
import sys


class ColoredFormatter(logging.Formatter):
    """Custom formatter that adds orange color for power state changes"""
    
    # ANSI color codes
    ORANGE = '\033[93m'  # Bright yellow/orange (closest to orange)
    RESET = '\033[0m'
    
    def format(self, record):
        # Check if this is a power state change or STANDBY detection message (WARNING level)
        msg = record.getMessage()
        if record.levelno == logging.WARNING and ('Power state changed' in msg or 'STANDBY mode' in msg):
            # Format with orange color
            original_msg = super().format(record)
            return f"{self.ORANGE}{original_msg}{self.RESET}"
        return super().format(record)


def setup_logging(log_level='INFO'):
    """Setup logging with configurable level"""
    level = getattr(logging, log_level.upper(), logging.INFO)
    
    root_logger = logging.getLogger()
    
    # Prevent duplicate handlers - clear existing handlers first
    if root_logger.handlers:
        root_logger.handlers.clear()
    
    # Set up console handler with detailed format
    console_handler = logging.StreamHandler()
    console_handler.setLevel(level)
    
    # Use colored formatter for terminals that support ANSI codes
    # Check if output is a TTY (terminal) to enable colors
    if sys.stdout.isatty():
        formatter = ColoredFormatter(
            '%(asctime)s - %(name)s - %(levelname)s - [%(filename)s:%(lineno)d] - %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )
    else:
        # Non-TTY output (e.g., logs redirected to file) - use plain formatter
        formatter = logging.Formatter(
            '%(asctime)s - %(name)s - %(levelname)s - [%(filename)s:%(lineno)d] - %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )
    
    console_handler.setFormatter(formatter)
    
    root_logger.setLevel(level)
    root_logger.addHandler(console_handler)
    
    # Suppress Flask/Werkzeug noise
    logging.getLogger('werkzeug').setLevel(logging.WARNING)
    
    return logging.getLogger(__name__)

