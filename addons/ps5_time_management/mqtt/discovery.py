"""User discovery from ps5-mqtt configuration"""
import os
import json
import logging

logger = logging.getLogger(__name__)


def discover_users_from_ps5_mqtt(discovered_users_set):
    """Discover users from ps5-mqtt configuration and MQTT topics
    
    Args:
        discovered_users_set: Set of discovered usernames to update
    """
    # Method 1: Try to read ps5-mqtt configuration file
    ps5_mqtt_config_paths = [
        '/config/addons_config/ps5_mqtt/options.json',
        '/data/options.json',  # ps5-mqtt might store config here
        '/addons/ps5_mqtt/options.json'
    ]
    
    for config_path in ps5_mqtt_config_paths:
        try:
            if os.path.exists(config_path):
                with open(config_path, 'r') as f:
                    ps5_config = json.load(f)
                    psn_accounts = ps5_config.get('psn_accounts', [])
                    for account in psn_accounts:
                        username = account.get('username')
                        if username:
                            discovered_users_set.add(username)
                            logger.info(f"Discovered user from ps5-mqtt config: {username}")
        except Exception as e:
            logger.debug(f"Could not read ps5-mqtt config from {config_path}: {e}")
    
    # Method 2: Scan MQTT topics for user activity
    # This will be populated as we receive MQTT messages
    logger.info(f"Currently discovered users: {list(discovered_users_set)}")

