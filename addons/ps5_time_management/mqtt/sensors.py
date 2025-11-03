"""MQTT sensor publishing for PS5 Time Management add-on"""
import json
import logging
from datetime import datetime

logger = logging.getLogger(__name__)

# These will be set by main.py via set_dependencies
time_manager = None
mqtt_client = None
mqtt_connected = False
config = {}
discovered_users = set()
published_sensors = set()
user_warning_until = {}


def set_dependencies(tm, mqtt, mqtt_conn, cfg, discovered, published, warning_until):
    """Set dependencies for sensor publishing"""
    global time_manager, mqtt_client, mqtt_connected, config
    global discovered_users, published_sensors, user_warning_until
    time_manager = tm
    mqtt_client = mqtt
    mqtt_connected = mqtt_conn
    config = cfg
    discovered_users = discovered
    published_sensors = published
    user_warning_until = warning_until


def publish_user_sensors(user):
    """Publish MQTT Discovery sensors for a user"""
    if not mqtt_connected or mqtt_client is None:
        logger.debug(f"Deferring discovery publish for {user} until MQTT connected")
        return
    discovery_topic = config.get('mqtt', {}).get('discovery_topic', 'homeassistant')
    
    # Sensor configurations for each user
    sensors = [
        {
            'name': f'PS5 {user} Daily Playtime',
            'unique_id': f'ps5_time_management_{user.lower()}_daily',
            'state_topic': f'ps5_time_management/{user}/daily',
            'unit_of_measurement': 'min',
            'icon': 'mdi:clock-outline',
            'device_class': 'duration'
        },
        {
            'name': f'PS5 {user} Weekly Playtime',
            'unique_id': f'ps5_time_management_{user.lower()}_weekly',
            'state_topic': f'ps5_time_management/{user}/weekly',
            'unit_of_measurement': 'min',
            'icon': 'mdi:calendar-week',
            'device_class': 'duration'
        },
        {
            'name': f'PS5 {user} Monthly Playtime',
            'unique_id': f'ps5_time_management_{user.lower()}_monthly',
            'state_topic': f'ps5_time_management/{user}/monthly',
            'unit_of_measurement': 'min',
            'icon': 'mdi:calendar-month',
            'device_class': 'duration'
        },
        {
            'name': f'PS5 {user} Time Remaining',
            'unique_id': f'ps5_time_management_{user.lower()}_remaining',
            'state_topic': f'ps5_time_management/{user}/remaining',
            'unit_of_measurement': 'min',
            'icon': 'mdi:timer-outline',
            'device_class': 'duration'
        },
        {
            'name': f'PS5 {user} Current Game',
            'unique_id': f'ps5_time_management_{user.lower()}_game',
            'state_topic': f'ps5_time_management/{user}/game',
            'icon': 'mdi:gamepad-variant'
        },
        {
            'name': f'PS5 {user} Session Active',
            'unique_id': f'ps5_time_management_{user.lower()}_active',
            'state_topic': f'ps5_time_management/{user}/active',
            'icon': 'mdi:play'
        },
        {
            'name': f'PS5 {user} Shutdown Warning',
            'unique_id': f'ps5_time_management_{user.lower()}_warning',
            'state_topic': f'ps5_time_management/{user}/warning',
            'entity_category': 'diagnostic',
            'binary_sensor': True,
            'device_class': 'problem'
        }
    ]
    
    # Publish each sensor configuration
    for sensor in sensors:
        config_topic = f"{discovery_topic}/sensor/{sensor['unique_id']}/config"
        
        sensor_config = {
            'name': sensor['name'],
            'unique_id': sensor['unique_id'],
            'state_topic': sensor['state_topic'],
            'device': {
                'identifiers': [f'ps5_time_management_{user.lower()}'],
                'name': f'PS5 Time Management - {user}',
                'model': 'PS5 Time Management',
                'manufacturer': 'PS5 Time Management Add-on'
            }
        }
        
        # Add optional fields
        if 'unit_of_measurement' in sensor:
            sensor_config['unit_of_measurement'] = sensor['unit_of_measurement']
        if 'device_class' in sensor:
            sensor_config['device_class'] = sensor['device_class']
        if 'icon' in sensor:
            sensor_config['icon'] = sensor['icon']
        # Discovery domain override for binary_sensor
        if sensor.get('binary_sensor'):
            config_topic = config_topic.replace('/sensor/', '/binary_sensor/')
            # For binary_sensor set payloads
            sensor_config['payload_on'] = 'ON'
            sensor_config['payload_off'] = 'OFF'
        
        try:
            mqtt_client.publish(config_topic, json.dumps(sensor_config), retain=True)
            published_sensors.add(sensor['unique_id'])
            logger.info(f"Published sensor config: {sensor['name']}")
        except Exception as e:
            logger.error(f"Failed to publish sensor config for {sensor['name']}: {e}")


def update_all_sensor_states():
    """Update MQTT sensor states for all discovered users"""
    for user in discovered_users:
        update_user_sensor_states(user)


def update_user_sensor_states(user):
    """Update MQTT sensor states for a specific user"""
    try:
        if not mqtt_connected or mqtt_client is None:
            logger.debug(f"Deferring state publish for {user} until MQTT connected")
            return
        # Get user stats using the correct methods
        daily_time = time_manager.get_user_time_today(user)
        weekly_time = time_manager.get_user_weekly_time(user)
        monthly_time = time_manager.get_user_monthly_time(user)
        
        # Get current session info
        current_session = None
        for session_id, session in time_manager.active_sessions.items():
            if session['user'] == user:
                current_session = session
                break
        
        # Calculate time remaining (using day-specific limit if set)
        daily_limit = time_manager.get_user_limit_for_today(user)
        if daily_limit is None:
            # Fallback to database global setting, then config default if no user limit set
            default_from_db = time_manager.get_global_setting('default_daily_limit_minutes')
            if default_from_db is not None:
                daily_limit = int(default_from_db)
            else:
                daily_limit = config.get('default_daily_limit_minutes', 120)
        
        if daily_limit is not None:
            time_remaining = max(0, daily_limit - daily_time)
        else:
            time_remaining = 0
        
        # Publish sensor states
        base_topic = f"ps5_time_management/{user}"
        
        # Daily playtime
        mqtt_client.publish(f"{base_topic}/daily", str(daily_time), retain=True)
        
        # Weekly playtime
        mqtt_client.publish(f"{base_topic}/weekly", str(weekly_time), retain=True)
        
        # Monthly playtime
        mqtt_client.publish(f"{base_topic}/monthly", str(monthly_time), retain=True)
        
        # Time remaining
        mqtt_client.publish(f"{base_topic}/remaining", str(time_remaining), retain=True)
        
        # Current game
        current_game = current_session['game'] if current_session else 'None'
        mqtt_client.publish(f"{base_topic}/game", current_game, retain=True)
        
        # Session active
        session_active = 'ON' if current_session else 'OFF'
        mqtt_client.publish(f"{base_topic}/active", session_active, retain=True)
        
        # Shutdown warning binary sensor
        warn_on = 'OFF'
        expiry = user_warning_until.get(user)
        if expiry and datetime.now() < expiry:
            warn_on = 'ON'
        mqtt_client.publish(f"{base_topic}/warning", warn_on, retain=True)
        
        logger.debug(f"Updated sensor states for {user}: daily={daily_time}, weekly={weekly_time}, monthly={monthly_time}, remaining={time_remaining}")
        
        # Log current session info for debugging
        if current_session:
            elapsed_minutes = (datetime.now() - current_session['start_time']).total_seconds() / 60
            logger.debug(f"Current session for {user}: {current_session['game']} (elapsed: {elapsed_minutes:.1f} min)")
        else:
            logger.debug(f"No active session for {user}")
        
    except Exception as e:
        logger.error(f"Failed to update sensor states for {user}: {e}")

