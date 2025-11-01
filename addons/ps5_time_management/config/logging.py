"""Logging configuration for PS5 Time Management add-on"""
import logging


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
    console_handler.setFormatter(logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - [%(filename)s:%(lineno)d] - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    ))
    
    root_logger.setLevel(level)
    root_logger.addHandler(console_handler)
    
    # Suppress Flask/Werkzeug noise
    logging.getLogger('werkzeug').setLevel(logging.WARNING)
    
    return logging.getLogger(__name__)

