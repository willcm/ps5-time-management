"""MQTT configuration for PS5 Time Management add-on"""
import os
import logging

logger = logging.getLogger(__name__)


def get_mqtt_config(config=None):
    """Get MQTT configuration from Home Assistant or manual config"""
    # Check for Home Assistant MQTT service configuration
    ha_mqtt_config = {
        'host': os.environ.get('MQTT_HOST'),
        'port': int(os.environ.get('MQTT_PORT', 1883)) if os.environ.get('MQTT_PORT') else 1883,
        'user': os.environ.get('MQTT_USERNAME'),
        'password': os.environ.get('MQTT_PASSWORD'),
        'discovery_topic': os.environ.get('DISCOVERY_TOPIC', 'homeassistant')
    }
    
    # Debug: Log all MQTT-related environment variables
    logger.info("MQTT Environment Variables:")
    for key, value in os.environ.items():
        if 'MQTT' in key.upper():
            logger.info(f"  {key}: '{value}'")
    
    # Also check for other common MQTT environment variables
    logger.info("All Environment Variables:")
    for key, value in os.environ.items():
        if any(keyword in key.upper() for keyword in ['MQTT', 'MOSQUITTO', 'BROKER']):
            logger.info(f"  {key}: '{value}'")
    
    # If Home Assistant provided MQTT config, use it
    if ha_mqtt_config['host']:
        logger.info("Using Home Assistant MQTT service configuration")
        return ha_mqtt_config
    
    # Try to read MQTT config from Home Assistant configuration files
    logger.info("Attempting to read MQTT config from Home Assistant files")
    try:
        # Check common Home Assistant config locations
        config_paths = [
            '/config/configuration.yaml',
            '/config/mqtt.yaml',
            '/data/options.json'  # This might contain MQTT config
        ]
        
        for config_path in config_paths:
            if os.path.exists(config_path):
                logger.info(f"Found config file: {config_path}")
                # Try to read and parse MQTT config from these files
                # This is a simplified approach - in practice, we'd need proper YAML parsing
                with open(config_path, 'r') as f:
                    content = f.read()
                    if 'mqtt:' in content.lower():
                        logger.info(f"Found MQTT configuration in {config_path}")
                        # For now, just log that we found it
                        break
    except Exception as e:
        logger.warning(f"Could not read Home Assistant config files: {e}")
    
    # Fall back to manual configuration
    config = config or {}
    mqtt_config = config.get('mqtt', {})
    manual_config = {
        'host': mqtt_config.get('host', 'core-mosquitto'),
        'port': int(mqtt_config.get('port', 1883)),
        'user': mqtt_config.get('user', ''),
        'password': mqtt_config.get('pass', ''),
        'discovery_topic': mqtt_config.get('discovery_topic', 'homeassistant')
    }
    
    # If no manual config provided, try anonymous connection first
    if not manual_config['user'] and not manual_config['password']:
        logger.info("No MQTT credentials provided, attempting anonymous connection")
        # Try without authentication first (like ps5-mqtt does)
        manual_config['user'] = None
        manual_config['password'] = None
    
    logger.info("Using manual MQTT configuration")
    return manual_config

