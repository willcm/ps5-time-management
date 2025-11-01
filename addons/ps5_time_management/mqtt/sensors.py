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
# Track last published values to ensure we never decrease (for state_class: total sensors)
last_published_values = {}  # {user: {'daily': 0, 'weekly': 0, 'monthly': 0, 'last_date': date}}


def set_dependencies(tm, mqtt, mqtt_conn, cfg, discovered, published, warning_until):
    """Set dependencies for sensor publishing"""
    global time_manager, mqtt_client, mqtt_connected, config
    global discovered_users, published_sensors, user_warning_until, last_published_values
    time_manager = tm
    mqtt_client = mqtt
    mqtt_connected = mqtt_conn
    config = cfg
    discovered_users = discovered
    published_sensors = published
    user_warning_until = warning_until
    # last_published_values is already initialized at module level, no need to reinitialize


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
            'device_class': 'duration',
            'state_class': 'total'
        },
        {
            'name': f'PS5 {user} Weekly Playtime',
            'unique_id': f'ps5_time_management_{user.lower()}_weekly',
            'state_topic': f'ps5_time_management/{user}/weekly',
            'unit_of_measurement': 'min',
            'icon': 'mdi:calendar-week',
            'device_class': 'duration',
            'state_class': 'total'
        },
        {
            'name': f'PS5 {user} Monthly Playtime',
            'unique_id': f'ps5_time_management_{user.lower()}_monthly',
            'state_topic': f'ps5_time_management/{user}/monthly',
            'unit_of_measurement': 'min',
            'icon': 'mdi:calendar-month',
            'device_class': 'duration',
            'state_class': 'total'
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
            'unique_id': f'ps5_time_management_{user.lower()}_session_active',
            'state_topic': f'ps5_time_management/{user}/active',
            'icon': 'mdi:play',
            'binary_sensor': True,
            'device_class': None  # No device class for generic binary sensor
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
        if 'state_class' in sensor:
            sensor_config['state_class'] = sensor['state_class']
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
    global last_published_values
    try:
        if not mqtt_connected or mqtt_client is None:
            logger.debug(f"Deferring state publish for {user} until MQTT connected")
            return
        # Get user stats using the correct methods
        daily_time = time_manager.get_user_time_today(user)
        weekly_time = time_manager.get_user_weekly_time(user)
        monthly_time = time_manager.get_user_monthly_time(user)
        
        # Ensure values never decrease (for state_class: total sensors)
        # This prevents the graph from going down when calculation errors occur
        # However, reset at midnight (new day) - check if date changed
        from datetime import date
        today = date.today()
        
        if user not in last_published_values:
            last_published_values[user] = {'daily': 0, 'weekly': 0, 'monthly': 0, 'last_date': today}
        
        # If it's a new day, reset daily (but keep weekly/monthly as they span multiple days)
        if last_published_values[user].get('last_date') != today:
            last_published_values[user]['daily'] = 0
            last_published_values[user]['last_date'] = today
            logger.debug(f"New day detected for {user}, resetting daily playtime baseline")
        
        # Only increase values - never decrease (Home Assistant state_class: total expects this)
        # But allow new day to reset daily value
        daily_time = max(daily_time, last_published_values[user].get('daily', 0) if last_published_values[user].get('last_date') == today else 0)
        weekly_time = max(weekly_time, last_published_values[user].get('weekly', 0))
        monthly_time = max(monthly_time, last_published_values[user].get('monthly', 0))
        
        # Update last published values
        last_published_values[user]['daily'] = daily_time
        last_published_values[user]['weekly'] = weekly_time
        last_published_values[user]['monthly'] = monthly_time
        
        # Get current session info
        current_session = None
        for session_id, session in time_manager.active_sessions.items():
            if session['user'] == user:
                current_session = session
                break
        
        # Calculate time remaining (using user's daily limit)
        user_limit_obj = time_manager.get_user_limit(user)
        # Handle both dict and old format
        if isinstance(user_limit_obj, dict):
            daily_limit = user_limit_obj.get('daily_limit_minutes', 120)
        elif user_limit_obj is not None:
            daily_limit = user_limit_obj
        else:
            daily_limit = 120
        time_remaining = max(0, daily_limit - daily_time)
        
        # Publish sensor states
        base_topic = f"ps5_time_management/{user}"
        
        # Daily playtime (ensured to only increase)
        mqtt_client.publish(f"{base_topic}/daily", str(int(daily_time)), retain=True)
        
        # Weekly playtime
        mqtt_client.publish(f"{base_topic}/weekly", str(weekly_time), retain=True)
        
        # Monthly playtime
        mqtt_client.publish(f"{base_topic}/monthly", str(monthly_time), retain=True)
        
        # Time remaining
        mqtt_client.publish(f"{base_topic}/remaining", str(time_remaining), retain=True)
        
        # Current game
        current_game = current_session['game'] if current_session else 'None'
        mqtt_client.publish(f"{base_topic}/game", current_game, retain=True)
        
        # Session active (binary sensor - retained so state persists after restart)
        session_active = 'ON' if current_session else 'OFF'
        mqtt_client.publish(f"{base_topic}/active", session_active, retain=True)
        logger.debug(f"Published session_active={session_active} for {user} (retained)")
        
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

