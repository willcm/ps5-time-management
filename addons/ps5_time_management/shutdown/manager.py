"""Shutdown management for PS5 Time Management add-on"""
import logging
from datetime import datetime, timedelta
from threading import Timer
import sqlite3

logger = logging.getLogger(__name__)

# Global state that needs to be accessed
time_manager = None  # Will be set by main
mqtt_client = None  # Will be set by main
user_warning_until = {}  # user -> datetime when warning expires


def set_dependencies(tm, mqtt):
    """Set the time manager and mqtt client dependencies"""
    global time_manager, mqtt_client
    time_manager = tm
    mqtt_client = mqtt


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


def apply_shutdown_policy(user: str, ps5_id: str, reason: str):
    """Apply shutdown policy: first time = warning, subsequent = immediate"""
    if has_shutdown_today(user):
        logger.info(f"User {user} already had shutdown today - enforcing immediate standby")
        enforce_standby(ps5_id, user, reason)
    else:
        logger.info(f"First shutdown of day for {user} - starting warning sequence")
        start_shutdown_warning(user, ps5_id)


def start_shutdown_warning(user: str, ps5_id: str):
    """Start 60-second warning sequence before standby"""
    if not mqtt_client:
        logger.error("MQTT client not initialized")
        return
    
    global user_warning_until
    warning_end = datetime.now() + timedelta(seconds=60)
    user_warning_until[user] = warning_end
    
    # Publish warning sensor state
    try:
        topic = f"homeassistant/binary_sensor/ps5_time_management_{user.lower().replace(' ', '_')}_shutdown_warning/state"
        mqtt_client.publish(topic, "ON", retain=True)
        logger.info(f"Published shutdown warning ON for {user}")
    except Exception as e:
        logger.error(f"Failed to publish warning sensor: {e}")
    
    # Schedule standby after 60 seconds
    def standby_after_delay():
        enforce_standby(ps5_id, user, 'time_limit')
    
    timer = Timer(60.0, standby_after_delay)
    timer.start()
    logger.info(f"Started 60-second shutdown warning for {user}, PS5 will go to standby at {warning_end}")


def enforce_standby(ps5_id: str, user: str | None = None, reason: str = 'manual_or_policy'):
    """Immediately enforce standby mode"""
    if not mqtt_client:
        logger.error("MQTT client not initialized")
        return
    
    global user_warning_until
    if user:
        # Clear warning sensor
        user_warning_until.pop(user, None)
        try:
            topic = f"homeassistant/binary_sensor/ps5_time_management_{user.lower().replace(' ', '_')}_shutdown_warning/state"
            mqtt_client.publish(topic, "OFF", retain=True)
            logger.info(f"Cleared shutdown warning for {user}")
        except Exception as e:
            logger.error(f"Failed to clear warning sensor: {e}")
        
        # Log the shutdown event
        log_shutdown_event(user, ps5_id, reason, 'standby')
    
    # Send STANDBY command
    try:
        topic_prefix = "ps5-mqtt"  # Will be configurable later
        standby_topic = f"{topic_prefix}/{ps5_id}/set"
        mqtt_client.publish(standby_topic, "STANDBY", retain=False)
        logger.info(f"Published STANDBY command to {standby_topic}")
        if user:
            logger.info(f"Enforced standby for {user} due to: {reason}")
    except Exception as e:
        logger.error(f"Failed to enforce standby: {e}")

