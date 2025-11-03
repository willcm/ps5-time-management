#!/usr/bin/env python3
"""
PS5 Time Management Add-on for Home Assistant
Tracks playtime by user and game, implements parental controls and time limits
"""

import os
import json
import sqlite3
import time
import glob
from datetime import datetime, timedelta
from threading import Thread, Timer
import threading
import paho.mqtt.client as mqtt
from flask import Flask, jsonify, request, render_template, url_for
from flask_cors import CORS
import logging
from flask import send_from_directory
from urllib.request import urlopen, Request

# Import from config modules
from config.logging import setup_logging
from config.loader import load_config as _load_config_from_module
from config.mqtt_config import get_mqtt_config as _get_mqtt_config_from_module

# Import from shutdown module
from shutdown.manager import (
    log_shutdown_event,
    has_shutdown_today,
    apply_shutdown_policy,
    start_shutdown_warning,
    enforce_standby,
    set_dependencies as set_shutdown_dependencies
)

# Import from utils modules
from utils.timers import check_timers as _check_timers
from utils.data_cleanup import clear_all_user_data as _clear_all_user_data

# Import from mqtt modules
from mqtt.discovery import discover_users_from_ps5_mqtt as _discover_users_from_ps5_mqtt
from mqtt.handler import (
    handle_device_update as _handle_device_update,
    handle_state_change as _handle_state_change,
    handle_game_change as _handle_game_change,
    handle_user_change as _handle_user_change,
    handle_activity_change as _handle_activity_change,
    set_dependencies as set_handler_dependencies
)
from mqtt.sensors import (
    publish_user_sensors as _publish_user_sensors,
    update_all_sensor_states as _update_all_sensor_states,
    update_user_sensor_states as _update_user_sensor_states,
    set_dependencies as set_sensor_dependencies
)

# Import from routes modules
from routes.api import register_routes as register_api_routes
from routes.web import register_routes as register_web_routes
from routes.static import register_routes as register_static_routes

# Import from models module
from models.time_manager import PS5TimeManager, set_latest_device_status

# Create logger - will be reconfigured with proper level after config load
logger = setup_logging()
# Explicitly register date adapter to avoid Python 3.12 deprecation warnings
sqlite3.register_adapter(datetime.date, lambda d: d.isoformat())

# Initialize Flask app
app = Flask(__name__, template_folder='templates')
CORS(app)

# Configuration
config = {}
mqtt_client = None
discovered_users = set()  # Set of discovered usernames
# Latest device status snapshot from ps5-mqtt
latest_device_status = {
    'ps5_id': None,
    'power': 'UNKNOWN',
    'device_status': 'offline',
    'activity': 'none',
    'players': [],
    'title_id': None,
    'title_name': None,
    'title_image': None,
    'last_update': None,
}
mqtt_connected = False
debug_user_name = None
user_warning_until = {}  # user -> datetime when warning expires

# Shutdown functions are now imported from shutdown.manager module
published_sensors = set()  # Track which sensors we've published via MQTT Discovery
current_session = {
    'user': None,
    'game': None,
    'start_time': None,
    'ps5_id': None
}

# PS5TimeManager class has been moved to models/time_manager.py
# Initialize time manager
time_manager = None
# Track sessions awaiting MQTT verification on startup
pending_session_restorations = {}  # ps5_id -> list of session dicts

def discover_users_from_ps5_mqtt():
    """Discover users from ps5-mqtt configuration and MQTT topics"""
    global discovered_users
    _discover_users_from_ps5_mqtt(discovered_users)


def publish_user_sensors(user):
    """Publish MQTT Discovery sensors for a user"""
    _publish_user_sensors(user)


def update_all_sensor_states():
    """Update MQTT sensor states for all discovered users"""
    _update_all_sensor_states()


def update_user_sensor_states(user):
    """Update MQTT sensor states for a specific user"""
    _update_user_sensor_states(user)

def on_connect(client, userdata, flags, reason_code, properties):
    """Callback when connected to MQTT broker"""
    global mqtt_connected
    mqtt_connected = True
    logger.info(f"MQTT on_connect callback: reason_code={reason_code}, flags={flags}")
    
    # Update shutdown manager with connected client
    set_shutdown_dependencies(time_manager, mqtt_client, True, config)
    
    # Update MQTT handler dependencies with connected client
    set_handler_dependencies(
        time_manager, mqtt_client, True, config, discovered_users, 
        latest_device_status, debug_user_name, 
        apply_shutdown_policy, start_shutdown_warning, 
        update_all_sensor_states, publish_user_sensors
    )
    
    # Update MQTT sensor dependencies with connected client
    set_sensor_dependencies(
        time_manager, mqtt_client, True, config, discovered_users, 
        published_sensors, user_warning_until
    )
    
    if reason_code == 0:
        logger.info("Connected to MQTT broker successfully")
        
        # Discover users from ps5-mqtt configuration
        discover_users_from_ps5_mqtt()
        
        # Subscribe to ps5-mqtt topics with QoS 1 to ensure we receive retained messages
        topic_prefix = config.get('mqtt_topic_prefix', 'ps5-mqtt')
        subscribe_topic = f"{topic_prefix}/#"
        logger.info(f"Subscribing to MQTT topic: {subscribe_topic} (QoS 1 for retained messages)")
        client.subscribe(subscribe_topic, qos=1)
        
        # If we have pending session restorations, log which PS5s we're waiting for
        if pending_session_restorations:
            logger.info(f"Waiting for retained MQTT messages from {len(pending_session_restorations)} PS5(s) to verify session restoration")
        
        logger.info(f"Subscribed to MQTT topics with prefix: {topic_prefix}")
        # Publish discovery for all known users now that we're connected
        try:
            if discovered_users:
                for user in list(discovered_users):
                    publish_user_sensors(user)
            # Immediately publish current states so entities have retained values
            update_all_sensor_states()
            
            # Log current active sessions after MQTT connection (restoration may happen via retained messages)
            if time_manager:
                # Give a moment for retained messages to arrive, then log sessions
                def log_sessions_delayed():
                    time.sleep(2)  # Wait 2 seconds for retained messages to arrive
                    if time_manager:
                        time_manager.log_all_active_sessions()
                threading.Thread(target=log_sessions_delayed, daemon=True).start()
        except Exception as e:
            logger.warning(f"Failed to publish discovery on connect: {e}")
    else:
        logger.error(f"Failed to connect to MQTT broker with code {reason_code}")

def on_message(client, userdata, msg):
    """Callback when message received from MQTT broker"""
    topic = msg.topic
    payload = msg.payload.decode('utf-8')
    
    # Log ALL MQTT messages we receive
    logger.info(f"MQTT MESSAGE RECEIVED - Topic: {topic}, Payload: {payload}")
    
    try:
        # Parse topic to get PS5 ID and early-ignore non-JSON topics
        parts = topic.split('/')
        # Ignore our own command/set subtopics before attempting JSON parse
        if len(parts) >= 3 and parts[2] in ('command', 'set'):
            return
        
        data = json.loads(payload)
        logger.debug(f"Parsed MQTT data: {data}")
        
        if len(parts) >= 2:
            ps5_id = parts[1]
            logger.debug(f"Extracted PS5 ID: {ps5_id}")
            
            # Handle the main ps5-mqtt/{device_id} topic which contains all device info
            if len(parts) == 2 and parts[0] == 'ps5-mqtt':
                logger.debug(f"Processing as device update for PS5 {ps5_id}")
                # Check if this is a retained message that can verify pending sessions
                handle_session_restoration(ps5_id, data)
                handle_device_update(ps5_id, data)
            else:
                logger.debug(f"Ignoring non-device topic: {parts}")
                
    except json.JSONDecodeError:
        logger.error(f"Failed to parse JSON from topic {topic}, payload: {payload}")
    except Exception as e:
        logger.error(f"Error handling MQTT message: {e}")

def handle_session_restoration(ps5_id, data):
    """Check if pending sessions should be restored based on MQTT retained message"""
    global pending_session_restorations, time_manager
    
    # Check if we have pending sessions for this PS5
    if ps5_id not in pending_session_restorations:
        return  # No pending sessions for this PS5
    
    pending_sessions = pending_session_restorations[ps5_id]
    if not pending_sessions:
        return  # Empty list
    
    # Get current power state from MQTT message
    power = data.get('power')
    device_status = data.get('device_status')
    activity = data.get('activity')
    players = data.get('players', [])
    
    logger.info(f"Checking session restoration for PS5 {ps5_id}: power={power}, device_status={device_status}, activity={activity}, players={players}")
    
    # According to ps5-mqtt-plugin-doc.txt:
    # - AWAKE = session should be active
    # - STANDBY = session should be ended
    # - UNKNOWN + offline = device unreachable, session should be ended
    
    if power == 'AWAKE' and device_status == 'online':
        # PS5 is still awake - restore sessions
        logger.info(f"PS5 {ps5_id} is AWAKE - restoring {len(pending_sessions)} session(s)")
        for session in pending_sessions:
            try:
                # Only restore if the user is still in the players list (or if activity is playing/idle)
                # This ensures we're restoring the right session
                user = session['user']
                should_restore = False
                
                if activity == 'playing' and user in players:
                    # User is actively playing - definitely restore
                    should_restore = True
                    logger.info(f"Restoring session for {user} - they are actively playing on PS5 {ps5_id}")
                elif activity in ['playing', 'idle']:
                    # PS5 is awake and active, but we can't verify user from players list
                    # Still restore - better to over-restore than miss sessions
                    # The session will be corrected when we get the next update with players
                    should_restore = True
                    logger.info(f"Restoring session for {user} - PS5 {ps5_id} is AWAKE (activity: {activity})")
                
                if should_restore:
                    time_manager.restore_session(
                        session['db_id'],
                        session['user'],
                        session['game'],
                        session['start_time'],
                        session['ps5_id']
                    )
                else:
                    # PS5 is awake but user not in players - mark as ended
                    logger.info(f"PS5 {ps5_id} is AWAKE but {user} not in players list - marking session as ended")
                    time_manager.mark_session_ended(session['db_id'], ended_normally=False)
            except Exception as e:
                logger.error(f"Failed to restore session {session.get('db_id')}: {e}")
    else:
        # PS5 is STANDBY or UNKNOWN - mark all sessions as ended
        logger.info(f"PS5 {ps5_id} is {power} (status: {device_status}) - marking {len(pending_sessions)} session(s) as ended")
        for session in pending_sessions:
            try:
                # Mark as ended with ended_normally=False since the plugin restarted
                time_manager.mark_session_ended(session['db_id'], ended_normally=False)
                logger.info(f"Marked session {session['db_id']} for {session['user']} as ended (PS5 went to {power})")
            except Exception as e:
                logger.error(f"Failed to mark session {session.get('db_id')} as ended: {e}")
    
    # Clear pending sessions for this PS5 - we've handled them
    del pending_session_restorations[ps5_id]
    logger.info(f"Cleared pending session restorations for PS5 {ps5_id}")
    
    # If all pending restorations are complete, log summary of active sessions
    if not pending_session_restorations and time_manager:
        time_manager.log_all_active_sessions()

def handle_device_update(ps5_id, data):
    """Handle complete device update from ps5-mqtt"""
    _handle_device_update(ps5_id, data)


def handle_state_change(ps5_id, data):
    """Handle PS5 state changes (on/off)"""
    _handle_state_change(ps5_id, data)


def handle_game_change(ps5_id, data):
    """Handle game changes"""
    _handle_game_change(ps5_id, data)


def handle_user_change(ps5_id, data):
    """Handle user changes"""
    _handle_user_change(ps5_id, data)


def handle_activity_change(ps5_id, data):
    """Handle activity changes (user presence, game activity)"""
    _handle_activity_change(ps5_id, data)

def check_timers():
    """Background thread to check timers and enforce limits"""
    _check_timers(time_manager, config, apply_shutdown_policy)

# Register all Flask routes
def register_all_routes():
    """Register all Flask routes from route modules"""
    # Register static file routes
    register_static_routes(app)
    
    # Register web page routes
    register_web_routes(app, time_manager, discovered_users)
    
    # Register API routes
    register_api_routes(app, time_manager, discovered_users, mqtt_connected, mqtt_client,
                       publish_user_sensors, update_user_sensor_states, 
                       latest_device_status, debug_user_name, config)

# Routes are registered via register_all_routes() which is called after time_manager is initialized

# All Flask routes have been moved to routes/ modules
# They are registered via register_all_routes() which is called after initialization

def clear_all_user_data():
    """Clear all historic data for all users"""
    return _clear_all_user_data(time_manager, discovered_users, update_all_sensor_states)

def load_config():
    """Load configuration from options.json"""
    global logger, debug_user_name
    
    config_dict = _load_config_from_module()
    
    # Setup logging based on config
    log_level = config_dict.get('log_level', 'INFO')
    logger = setup_logging(log_level)
    logger.info(f"Configuration loaded")
    logger.debug(f"Full configuration: {json.dumps(config_dict, indent=2)}")
    # Set per-user debug if provided
    debug_user_name = config_dict.get('debug_user')
    
    # Ensure always-enabled options default to True
    config_dict.setdefault('enable_parental_controls', True)
    config_dict.setdefault('graceful_shutdown_enabled', True)
    config_dict.setdefault('graceful_shutdown_warnings', True)
    
    return config_dict

def get_mqtt_config():
    """Get MQTT configuration from Home Assistant or manual config"""
    return _get_mqtt_config_from_module(config)

def main():
    """Main entry point"""
    global config, time_manager, mqtt_client
    
    # Load configuration
    config = load_config()
    logger.info("Configuration loaded")
    
    # Initialize time manager
    db_path = config.get('database_path', '/data/ps5_time_management.db')
    time_manager = PS5TimeManager(db_path)
    
    # Register all Flask routes now that time_manager is initialized
    register_all_routes()
    
    # Load any previously persisted users (defers publishing until MQTT is connected)
    try:
        persisted_users = time_manager.load_users()
        if persisted_users:
            for user in persisted_users:
                if user not in discovered_users:
                    discovered_users.add(user)
            logger.info(f"Loaded persisted users from DB: {persisted_users}")
        else:
            logger.info("No persisted users found in DB yet")
    except Exception as e:
        logger.warning(f"Failed to initialize users from DB: {e}")
    
    # Load active sessions from database for restoration
    global pending_session_restorations
    try:
        active_sessions = time_manager.get_active_sessions_from_db()
        if active_sessions:
            logger.info(f"Found {len(active_sessions)} active session(s) in database that need verification")
            # Group sessions by PS5 ID
            for session in active_sessions:
                ps5_id = session['ps5_id']
                if ps5_id not in pending_session_restorations:
                    pending_session_restorations[ps5_id] = []
                pending_session_restorations[ps5_id].append(session)
            logger.info(f"Sessions grouped by PS5: {[(ps5_id, len(sessions)) for ps5_id, sessions in pending_session_restorations.items()]}")
        else:
            logger.info("No active sessions found in database")
    except Exception as e:
        logger.warning(f"Failed to load active sessions from DB: {e}")
    
    # Get MQTT configuration (automatic or manual)
    mqtt_config = get_mqtt_config()
    
    logger.info(f"MQTT Configuration: {mqtt_config['host']}:{mqtt_config['port']}")
    logger.debug(f"Full MQTT config: {mqtt_config}")
    
    # Initialize shutdown manager dependencies
    set_shutdown_dependencies(time_manager, None, False, config)  # Will update mqtt_client and mqtt_connected after connection
    
    # Initialize MQTT handler dependencies (will update mqtt_client after connection)
    set_handler_dependencies(
        time_manager, None, False, config, discovered_users, 
        latest_device_status, debug_user_name, 
        apply_shutdown_policy, start_shutdown_warning, 
        update_all_sensor_states, publish_user_sensors
    )
    
    # Initialize MQTT sensor dependencies (will update mqtt_client after connection)
    set_sensor_dependencies(
        time_manager, None, False, config, discovered_users, 
        published_sensors, user_warning_until
    )
    
    # Set up MQTT client
    mqtt_client = mqtt.Client(client_id="ps5_time_management", callback_api_version=mqtt.CallbackAPIVersion.VERSION2)
    mqtt_client.on_connect = on_connect
    mqtt_client.on_message = on_message
    
    if mqtt_config['user']:
        mqtt_client.username_pw_set(mqtt_config['user'], mqtt_config['password'])
        logger.debug(f"MQTT authentication enabled for user: {mqtt_config['user']}")
    
    # Connect to MQTT broker
    try:
        mqtt_client.connect(mqtt_config['host'], mqtt_config['port'], 60)
        mqtt_client.loop_start()
        logger.info(f"Connected to MQTT broker at {mqtt_config['host']}:{mqtt_config['port']}")
    except Exception as e:
        logger.error(f"Failed to connect to MQTT broker: {e}")
        logger.error("Make sure MQTT broker is running and accessible")
    
    # Start timer checking thread
    timer_thread = Thread(target=check_timers, daemon=True)
    timer_thread.start()
    
    # Start periodic sensor updates
    def periodic_sensor_update():
        """Update sensor states every 30 seconds"""
        while True:
            try:
                time.sleep(30)
                if discovered_users and mqtt_client:
                    update_all_sensor_states()
            except Exception as e:
                logger.error(f"Error in periodic sensor update: {e}")
    
    # Start sensor update thread
    sensor_thread = Thread(target=periodic_sensor_update, daemon=True)
    sensor_thread.start()
    logger.info("Started periodic sensor update thread")
    
    # Start Flask app
    port = int(os.environ.get('PORT', 8080))
    logger.info(f"Starting Flask app on port {port}")
    app.run(host='0.0.0.0', port=port, threaded=True)

if __name__ == '__main__':
    main()

