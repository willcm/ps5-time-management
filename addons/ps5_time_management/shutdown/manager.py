"""Shutdown management for PS5 Time Management add-on"""
import logging
from datetime import datetime, timedelta
from threading import Timer
import sqlite3
import paho.mqtt.client as mqtt

logger = logging.getLogger(__name__)

# Global state that needs to be accessed
time_manager = None  # Will be set by main
mqtt_client = None  # Will be set by main
mqtt_connected = False  # Will be set by main
config_dict = {}  # Will be set by main
user_warning_until = {}  # user -> datetime when warning expires


def set_dependencies(tm, mqtt, mqtt_conn, cfg):
    """Set the time manager, mqtt client, connection status, and config dependencies"""
    global time_manager, mqtt_client, mqtt_connected, config_dict
    time_manager = tm
    mqtt_client = mqtt
    mqtt_connected = mqtt_conn
    config_dict = cfg


def log_shutdown_event(user: str, ps5_id: str, reason: str, mode: str):
    """Log a shutdown event to the database"""
    if not time_manager:
        logger.error("Time manager not initialized")
        return
    try:
        conn = sqlite3.connect(time_manager.db_path)
        c = conn.cursor()
        c.execute('''INSERT INTO shutdown_events (user, ps5_id, reason, mode) VALUES (?, ?, ?, ?)''',
                  (user, ps5_id, reason, mode))
        conn.commit()
        conn.close()
        logger.info(f"Logged shutdown event: user={user}, reason={reason}, mode={mode}")
    except Exception as e:
        logger.warning(f"Failed to log shutdown event for {user}: {e}")


def has_shutdown_today(user: str) -> bool:
    """Return True if we have already enforced a shutdown for this user today."""
    if not time_manager:
        return False
    try:
        today = datetime.now().date().isoformat()
        conn = sqlite3.connect(time_manager.db_path)
        c = conn.cursor()
        c.execute('''SELECT 1 FROM shutdown_events 
                     WHERE user=? AND substr(created_at,1,10)=? 
                     LIMIT 1''', (user, today))
        row = c.fetchone()
        conn.close()
        return row is not None
    except Exception as e:
        logger.warning(f"Failed to check shutdown today for {user}: {e}")
        return False


def apply_shutdown_policy(user: str, ps5_id: str, reason: str, immediate: bool = False):
    """Apply shutdown policy: first time = warning, subsequent = immediate
    
    Args:
        user: Username
        ps5_id: PS5 device ID
        reason: Reason for shutdown
        immediate: If True, skip warning and enforce standby immediately (e.g., for 0-minute days)
    """
    # If immediate shutdown requested (e.g., 0-minute day), skip warning
    if immediate:
        logger.info(f"Immediate shutdown requested for {user} (reason: {reason}) - enforcing standby now")
        enforce_standby(ps5_id, user, reason)
        return
    
    if has_shutdown_today(user):
        logger.info(f"User {user} already had shutdown today - enforcing immediate standby")
        enforce_standby(ps5_id, user, reason)
    else:
        logger.info(f"First shutdown of day for {user} - starting warning sequence")
        start_shutdown_warning(user, ps5_id)


def start_shutdown_warning(user: str, ps5_id: str):
    """Start warning sequence before standby (duration from config)"""
    if not mqtt_client:
        logger.error("MQTT client not initialized")
        return
    
    # Get warning duration from database, fallback to config (default to 1 minute / 60 seconds)
    warning_from_db = time_manager.get_global_setting('warning_before_shutdown_minutes')
    if warning_from_db is not None:
        warning_minutes = int(warning_from_db)
    else:
        warning_minutes = config_dict.get('warning_before_shutdown_minutes', 1)
    warning_seconds = warning_minutes * 60
    
    global user_warning_until
    warning_end = datetime.now() + timedelta(seconds=warning_seconds)
    user_warning_until[user] = warning_end
    
    # Publish warning sensor state (matching format used in publish_user_sensors)
    try:
        topic = f"ps5_time_management/{user}/warning"
        mqtt_client.publish(topic, "ON", retain=True)
        logger.info(f"Published shutdown warning ON for {user}")
    except Exception as e:
        logger.error(f"Failed to publish warning sensor: {e}")
    
    # Schedule standby after warning period
    def standby_after_delay():
        enforce_standby(ps5_id, user, 'time_limit')
    
    timer = Timer(warning_seconds, standby_after_delay)
    timer.start()
    logger.info(f"Started {warning_minutes}-minute shutdown warning for {user}, PS5 will go to standby at {warning_end}")


def enforce_standby(ps5_id: str, user: str | None = None, reason: str = 'manual_or_policy'):
    """Immediately enforce standby mode"""
    if not mqtt_client:
        logger.error("MQTT client not initialized")
        return
    
    if not mqtt_connected:
        logger.error("MQTT client not connected - cannot send standby command")
        return
    
    global user_warning_until
    if user:
        # Clear warning sensor (matching format used in publish_user_sensors)
        user_warning_until.pop(user, None)
        try:
            topic = f"ps5_time_management/{user}/warning"
            mqtt_client.publish(topic, "OFF", retain=True)
            logger.info(f"Cleared shutdown warning for {user}")
        except Exception as e:
            logger.error(f"Failed to clear warning sensor: {e}")
        
        # Log the shutdown event
        log_shutdown_event(user, ps5_id, reason, 'standby')
    
    # Send STANDBY command
    try:
        topic_prefix = config_dict.get('mqtt_topic_prefix', 'ps5-mqtt')
        standby_topic = f"{topic_prefix}/{ps5_id}/set/power"
        # Use QoS 0 for commands (as per ps5-mqtt plugin documentation)
        result = mqtt_client.publish(standby_topic, "STANDBY", qos=0, retain=False)
        
        # Check if publish was successful
        if result.rc == mqtt.MQTT_ERR_SUCCESS:
            logger.info(f"Published STANDBY command to {standby_topic} (message_id={result.mid})")
            if user:
                logger.info(f"Enforced standby for {user} due to: {reason}")
        else:
            logger.error(f"Failed to publish STANDBY command - return code: {result.rc}")
    except Exception as e:
        logger.error(f"Failed to enforce standby: {e}", exc_info=True)

