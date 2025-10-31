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
            
            # Handle clear_all_stats option - import here to avoid circular dependency
            if config.get('clear_all_stats', False):
                logger.warning("Clear all stats option detected - clearing all user data")
                # Import here to avoid circular dependency
                from main import clear_all_user_data
                clear_all_user_data()
                # Reset the option to prevent repeated clearing
                config['clear_all_stats'] = False
                with open(config_path, 'w') as f:
                    json.dump(config, f, indent=2)
                logger.info("Cleared all stats and reset option")
            
            return config
    
    logger.warning(f"Configuration file not found at {config_path}, using defaults")
    return {}

