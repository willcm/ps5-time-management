"""Configuration loading for PS5 Time Management add-on"""
import os
import json
import logging

logger = logging.getLogger(__name__)


def load_config():
    """Load configuration from options.json"""
    from config.logging import setup_logging
    
    config_path = '/data/options.json'
    if os.path.exists(config_path):
        with open(config_path, 'r') as f:
            config = json.load(f)
            
            # Setup logging based on config
            log_level = config.get('log_level', 'INFO')
            logger = setup_logging(log_level)
            logger.info(f"Configuration loaded from {config_path}")
            logger.debug(f"Full configuration: {json.dumps(config, indent=2)}")
            # Set per-user debug if provided
            global debug_user_name
            debug_user_name = config.get('debug_user')
            
            # Ensure always-enabled options default to True
            config.setdefault('enable_parental_controls', True)
            config.setdefault('graceful_shutdown_enabled', True)
            config.setdefault('graceful_shutdown_warnings', True)
            
            return config
    
    logger.warning(f"Configuration file not found at {config_path}, using defaults")
    return {}

