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
from threading import Thread
import paho.mqtt.client as mqtt
from flask import Flask, jsonify, request, render_template
from flask_cors import CORS
import logging

# Configure logging - will be updated after config is loaded
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

# Create logger - will be reconfigured with proper level after config load
logger = setup_logging()

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
published_sensors = set()  # Track which sensors we've published via MQTT Discovery
current_session = {
    'user': None,
    'game': None,
    'start_time': None,
    'ps5_id': None
}

class PS5TimeManager:
    def __init__(self, db_path):
        self.db_path = db_path
        self.init_database()
        self.active_sessions = {}
        self.user_limits = {}
        self.timer_thread = None
    
    def add_user_if_new(self, user: str) -> None:
        """Persist a discovered user if not already stored."""
        if not user:
            return
        try:
            conn = sqlite3.connect(self.db_path)
            c = conn.cursor()
            c.execute('INSERT OR IGNORE INTO users (user) VALUES (?)', (user,))
            conn.commit()
            conn.close()
        except Exception as e:
            logger.warning(f"Failed to persist user '{user}': {e}")
    
    def load_users(self):
        """Load all persisted users from the database."""
        try:
            conn = sqlite3.connect(self.db_path)
            c = conn.cursor()
            c.execute('SELECT user FROM users')
            rows = c.fetchall()
            conn.close()
            return [row[0] for row in rows]
        except Exception as e:
            logger.warning(f"Failed to load users from database: {e}")
            return []
        
    def init_database(self):
        """Initialize SQLite database with required tables"""
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        
        # Check if user_limits table exists with old schema and migrate
        try:
            c.execute("PRAGMA table_info(user_limits)")
            columns = c.fetchall()
            if columns:
                # Check if old schema exists (has both id and user as primary keys)
                has_id = any(col[1] == 'id' for col in columns)
                has_user = any(col[1] == 'user' for col in columns)
                if has_id and has_user:
                    logging.info("Migrating user_limits table from old schema")
                    # Create new table with correct schema
                    c.execute('''CREATE TABLE user_limits_new
                                 (user TEXT PRIMARY KEY,
                                  daily_limit_minutes INTEGER,
                                  weekly_limit_minutes INTEGER,
                                  monthly_limit_minutes INTEGER,
                                  current_daily_time INTEGER DEFAULT 0,
                                  current_weekly_time INTEGER DEFAULT 0,
                                  current_monthly_time INTEGER DEFAULT 0,
                                  reset_date DATE,
                                  enabled BOOLEAN DEFAULT 1)''')
                    # Copy data from old table
                    c.execute('''INSERT INTO user_limits_new 
                                 (user, daily_limit_minutes, weekly_limit_minutes, 
                                  monthly_limit_minutes, current_daily_time, 
                                  current_weekly_time, current_monthly_time, 
                                  reset_date, enabled)
                                 SELECT user, daily_limit_minutes, weekly_limit_minutes,
                                        monthly_limit_minutes, current_daily_time,
                                        current_weekly_time, current_monthly_time,
                                        reset_date, enabled
                                 FROM user_limits''')
                    # Drop old table and rename new one
                    c.execute('DROP TABLE user_limits')
                    c.execute('ALTER TABLE user_limits_new RENAME TO user_limits')
                    conn.commit()
                    logging.info("Successfully migrated user_limits table")
        except Exception as e:
            logging.warning(f"Migration check failed: {e}")
            # Continue with normal table creation
        
        # Sessions table - individual gaming sessions
        c.execute('''CREATE TABLE IF NOT EXISTS sessions
                     (id INTEGER PRIMARY KEY AUTOINCREMENT,
                      user TEXT NOT NULL,
                      game TEXT,
                      start_time TIMESTAMP NOT NULL,
                      end_time TIMESTAMP,
                      duration_seconds INTEGER,
                      ps5_id TEXT,
                      ended_normally BOOLEAN DEFAULT 1)''')
        
        # User stats table - aggregated statistics
        c.execute('''CREATE TABLE IF NOT EXISTS user_stats
                     (id INTEGER PRIMARY KEY AUTOINCREMENT,
                      user TEXT NOT NULL,
                      date DATE NOT NULL,
                      total_minutes INTEGER DEFAULT 0,
                      games_played TEXT,
                      session_count INTEGER DEFAULT 0)''')
        
        # Game stats table - per-game statistics
        c.execute('''CREATE TABLE IF NOT EXISTS game_stats
                     (id INTEGER PRIMARY KEY AUTOINCREMENT,
                      user TEXT NOT NULL,
                      game TEXT NOT NULL,
                      date DATE NOT NULL,
                      minutes_played INTEGER DEFAULT 0)''')
        
        # User limits table - configured time limits
        c.execute('''CREATE TABLE IF NOT EXISTS user_limits
                     (user TEXT PRIMARY KEY,
                      daily_limit_minutes INTEGER,
                      weekly_limit_minutes INTEGER,
                      monthly_limit_minutes INTEGER,
                      current_daily_time INTEGER DEFAULT 0,
                      current_weekly_time INTEGER DEFAULT 0,
                      current_monthly_time INTEGER DEFAULT 0,
                      reset_date DATE,
                      enabled BOOLEAN DEFAULT 1)''')
        
        # Notifications table - warnings and alerts
        c.execute('''CREATE TABLE IF NOT EXISTS notifications
                     (id INTEGER PRIMARY KEY AUTOINCREMENT,
                      user TEXT,
                      type TEXT,
                      message TEXT,
                      timestamp TIMESTAMP,
                      read BOOLEAN DEFAULT 0)''')
        
        # Users table - persist discovered users so we don't depend on live discovery
        c.execute('''CREATE TABLE IF NOT EXISTS users
                     (user TEXT PRIMARY KEY)''')
        
        conn.commit()
        conn.close()
        logger.info("Database initialized successfully")
    
    def start_session(self, user, game, ps5_id):
        """Start a new gaming session"""
        # Prevent duplicate sessions for same user on same PS5
        for session_id, s in self.active_sessions.items():
            if s['user'] == user and s.get('ps5_id') == ps5_id:
                logger.info(f"Duplicate session suppressed for {user} on PS5 {ps5_id} (existing session: {session_id})")
                return False
        
        session_id = f"{ps5_id}:{user}:{int(time.time())}"
        self.active_sessions[session_id] = {
            'user': user,
            'game': game,
            'start_time': datetime.now(),
            'ps5_id': ps5_id,
            'warnings_sent': []
        }
        
        logger.info(f"Started session for user {user} playing {game}")
        return session_id
    
    def end_session(self, session_id):
        """End a gaming session and save to database"""
        if session_id not in self.active_sessions:
            logger.warning(f"Session {session_id} not found")
            return False
        
        session = self.active_sessions.pop(session_id)
        user = session['user']
        game = session['game']
        start_time = session['start_time']
        end_time = datetime.now()
        duration = (end_time - start_time).total_seconds()
        
        # Save to database
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        
        c.execute('''INSERT INTO sessions 
                     (user, game, start_time, end_time, duration_seconds, ps5_id)
                     VALUES (?, ?, ?, ?, ?, ?)''',
                 (user, game, start_time, end_time, int(duration), session['ps5_id']))
        
        # Update daily stats - use proper UPSERT logic
        today = start_time.date()
        
        # First, try to get existing stats
        c.execute('''SELECT total_minutes, session_count FROM user_stats 
                     WHERE user=? AND date=?''',
                 (user, today))
        
        result = c.fetchone()
        if result:
            # Update existing record
            existing_minutes, existing_sessions = result
            new_minutes = existing_minutes + int(duration/60)
            new_sessions = existing_sessions + 1
            
            c.execute('''UPDATE user_stats 
                         SET total_minutes=?, session_count=? 
                         WHERE user=? AND date=?''',
                     (new_minutes, new_sessions, user, today))
        else:
            # Insert new record
            c.execute('''INSERT INTO user_stats 
                         (user, date, total_minutes, session_count)
                         VALUES (?, ?, ?, ?)''',
                     (user, today, int(duration/60), 1))
        
        # Update game stats - use proper UPSERT logic
        c.execute('''SELECT minutes_played FROM game_stats 
                     WHERE user=? AND game=? AND date=?''',
                 (user, game, today))
        
        result = c.fetchone()
        if result:
            # Update existing record
            existing_minutes = result[0]
            new_minutes = existing_minutes + int(duration/60)
            
            c.execute('''UPDATE game_stats 
                         SET minutes_played=? 
                         WHERE user=? AND game=? AND date=?''',
                     (new_minutes, user, game, today))
        else:
            # Insert new record
            c.execute('''INSERT INTO game_stats 
                         (user, game, date, minutes_played)
                         VALUES (?, ?, ?, ?)''',
                     (user, game, today, int(duration/60)))
        
        conn.commit()
        conn.close()
        
        logger.info(f"Ended session for user {user} playing {game} ({int(duration/60)} minutes)")
        return True
    
    def get_user_time_today(self, user):
        """Get total time played today by user (including active sessions)"""
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        today = datetime.now().date()
        
        # Get completed sessions from database
        c.execute('''SELECT total_minutes FROM user_stats 
                     WHERE user=? AND date=?''',
                 (user, today))
        
        result = c.fetchone()
        completed_time = result[0] if result and result[0] is not None else 0
        conn.close()
        
        # Add time from active sessions
        active_time = 0
        active_count = 0
        for session_id, session in self.active_sessions.items():
            if session['user'] == user:
                # Calculate time elapsed in current session
                elapsed = (datetime.now() - session['start_time']).total_seconds()
                session_minutes = elapsed / 60
                active_time += session_minutes
                active_count += 1
                logger.debug(f"Active session for {user}: {session['game']} - {session_minutes:.1f} minutes elapsed")
        
        total_time = completed_time + active_time
        logger.info(f"User {user} time today: {completed_time} min completed (from DB) + {active_time:.1f} min active ({active_count} sessions) = {total_time:.1f} min total")
        return int(round(total_time))  # Round instead of truncate for better accuracy
    
    def get_user_weekly_time(self, user):
        """Get total time played this week by user (including active sessions)"""
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        
        # Calculate week start (Monday)
        today = datetime.now().date()
        week_start = today - timedelta(days=today.weekday())
        
        # Get completed sessions from database for this week
        c.execute('''SELECT SUM(total_minutes) FROM user_stats 
                     WHERE user=? AND date >= ?''',
                 (user, week_start))
        
        result = c.fetchone()
        completed_time = result[0] if result and result[0] is not None else 0
        conn.close()
        
        # Add time from active sessions (if they started this week)
        active_time = 0
        for session_id, session in self.active_sessions.items():
            if session['user'] == user:
                session_date = session['start_time'].date()
                if session_date >= week_start:  # Only count sessions from this week
                    elapsed = (datetime.now() - session['start_time']).total_seconds()
                    active_time += elapsed / 60  # Convert to minutes
        
        total_time = completed_time + active_time
        logger.info(f"User {user} weekly time: {completed_time} min completed (from DB) + {active_time:.1f} min active = {total_time:.1f} min total")
        return int(round(total_time))
    
    def get_user_monthly_time(self, user):
        """Get total time played this month by user (including active sessions)"""
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        
        # Calculate month start
        today = datetime.now().date()
        month_start = today.replace(day=1)
        
        # Get completed sessions from database for this month
        c.execute('''SELECT SUM(total_minutes) FROM user_stats 
                     WHERE user=? AND date >= ?''',
                 (user, month_start))
        
        result = c.fetchone()
        completed_time = result[0] if result and result[0] is not None else 0
        conn.close()
        
        # Add time from active sessions (if they started this month)
        active_time = 0
        for session_id, session in self.active_sessions.items():
            if session['user'] == user:
                session_date = session['start_time'].date()
                if session_date >= month_start:  # Only count sessions from this month
                    elapsed = (datetime.now() - session['start_time']).total_seconds()
                    active_time += elapsed / 60  # Convert to minutes
        
        total_time = completed_time + active_time
        logger.info(f"User {user} monthly time: {completed_time} min completed (from DB) + {active_time:.1f} min active = {total_time:.1f} min total")
        return int(round(total_time))
    
    def get_top_games(self, user, days=30, limit=10):
        """Get top games played by user in the last N days"""
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        start_date = (datetime.now() - timedelta(days=days)).date()
        
        c.execute('''SELECT game, SUM(minutes_played) as total 
                     FROM game_stats 
                     WHERE user=? AND date >= ? 
                     GROUP BY game 
                     ORDER BY total DESC 
                     LIMIT ?''',
                 (user, start_date, limit))
        
        results = c.fetchall()
        conn.close()
        
        return [{'game': row[0], 'minutes': row[1]} for row in results]
    
    def get_user_limit(self, user):
        """Get configured time limit for user"""
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        
        c.execute('''SELECT daily_limit_minutes, enabled 
                     FROM user_limits 
                     WHERE user=?''',
                 (user,))
        
        result = c.fetchone()
        conn.close()
        
        if result and result[1]:  # enabled
            return result[0]
        return None
    
    def set_user_limit(self, user, daily_minutes, enabled=True):
        """Set time limit for user"""
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        
        c.execute('''INSERT OR REPLACE INTO user_limits 
                     (user, daily_limit_minutes, enabled)
                     VALUES (?, ?, ?)''',
                 (user, daily_minutes, enabled))
        
        conn.commit()
        conn.close()
        
        logger.info(f"Set limit for user {user}: {daily_minutes} minutes/day")
    
    def check_limit_exceeded(self, user):
        """Check if user has exceeded their time limit"""
        limit = self.get_user_limit(user)
        if not limit:
            return False
        
        time_today = self.get_user_time_today(user)
        return time_today >= limit
    
    def add_notification(self, user, type, message):
        """Add a notification for user"""
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        
        c.execute('''INSERT INTO notifications 
                     (user, type, message, timestamp)
                     VALUES (?, ?, ?, ?)''',
                 (user, type, message, datetime.now()))
        
        conn.commit()
        conn.close()

# Initialize time manager
time_manager = None

def discover_users_from_ps5_mqtt():
    """Discover users from ps5-mqtt configuration and MQTT topics"""
    global discovered_users
    
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
                            discovered_users.add(username)
                            logger.info(f"Discovered user from ps5-mqtt config: {username}")
        except Exception as e:
            logger.debug(f"Could not read ps5-mqtt config from {config_path}: {e}")
    
    # Method 2: Scan MQTT topics for user activity
    # This will be populated as we receive MQTT messages
    logger.info(f"Currently discovered users: {list(discovered_users)}")

def on_connect(client, userdata, flags, reason_code, properties):
    """Callback when connected to MQTT broker"""
    logger.info(f"MQTT on_connect callback: reason_code={reason_code}, flags={flags}")
    
    if reason_code == 0:
        logger.info("Connected to MQTT broker successfully")
        
        # Discover users from ps5-mqtt configuration
        discover_users_from_ps5_mqtt()
        
        # Subscribe to ps5-mqtt topics
        topic_prefix = config.get('mqtt_topic_prefix', 'ps5-mqtt')
        subscribe_topic = f"{topic_prefix}/#"
        logger.info(f"Subscribing to MQTT topic: {subscribe_topic}")
        client.subscribe(subscribe_topic)
        
        logger.info(f"Subscribed to MQTT topics with prefix: {topic_prefix}")
    else:
        logger.error(f"Failed to connect to MQTT broker with code {reason_code}")

def on_message(client, userdata, msg):
    """Callback when message received from MQTT broker"""
    topic = msg.topic
    payload = msg.payload.decode('utf-8')
    
    # Log ALL MQTT messages we receive
    logger.info(f"MQTT MESSAGE RECEIVED - Topic: {topic}, Payload: {payload}")
    
    try:
        data = json.loads(payload)
        logger.info(f"Parsed MQTT data: {data}")
        
        # Parse topic to get PS5 ID
        parts = topic.split('/')
        if len(parts) >= 2:
            ps5_id = parts[1]
            logger.info(f"Extracted PS5 ID: {ps5_id}")
            
            # Handle the main ps5-mqtt/{device_id} topic which contains all device info
            if len(parts) == 2 and parts[0] == 'ps5-mqtt':
                logger.info(f"Processing as device update for PS5 {ps5_id}")
                handle_device_update(ps5_id, data)
            else:
                logger.info(f"Topic doesn't match expected pattern. Parts: {parts}")
                
    except json.JSONDecodeError:
        logger.error(f"Failed to parse JSON from topic {topic}, payload: {payload}")
    except Exception as e:
        logger.error(f"Error handling MQTT message: {e}")

def handle_device_update(ps5_id, data):
    """Handle complete device update from ps5-mqtt"""
    logger.info(f"Processing device update for PS5 {ps5_id}: {data}")
    
    # Extract players from the message
    players = data.get('players', [])
    # Update latest device status snapshot
    try:
        latest_device_status.update({
            'ps5_id': ps5_id,
            'power': data.get('power', latest_device_status.get('power')),
            'device_status': data.get('device_status', latest_device_status.get('device_status')),
            'activity': data.get('activity', latest_device_status.get('activity')),
            'players': players or [],
            'title_id': data.get('title_id'),
            'title_name': data.get('title_name'),
            'title_image': data.get('title_image'),
            'last_update': datetime.now().isoformat()
        })
    except Exception as e:
        logger.warning(f"Failed updating latest device status: {e}")
    if players:
        for player in players:
            if player and player not in discovered_users:
                discovered_users.add(player)
                # Persist the discovered user so it survives restarts/updates
                time_manager.add_user_if_new(player)
                logger.info(f"Discovered new user: {player}")
                # Publish sensors for new user
                publish_user_sensors(player)
    
    # Handle activity changes
    activity = data.get('activity')
    if activity == 'playing' and players:
        # Start tracking session for active players
        for player in players:
            if player:
                game_name = data.get('title_name', 'Unknown Game')
                session_id = time_manager.start_session(player, game_name, ps5_id)
                if session_id:
                    logger.info(f"Started session for {player} playing {game_name} (ID: {session_id})")
                # else: duplicate suppressed (logged in start_session)
    elif activity in ['idle', 'none']:
        # End sessions for this PS5
        for session_id, session in list(time_manager.active_sessions.items()):
            if session['ps5_id'] == ps5_id:
                time_manager.end_session(session_id)
                logger.info(f"Ended session for PS5 {ps5_id}")
    
    # Handle power state
    power = data.get('power')
    if power == 'STANDBY':
        # End all sessions for this PS5 when it goes to standby
        for session_id, session in list(time_manager.active_sessions.items()):
            if session['ps5_id'] == ps5_id:
                time_manager.end_session(session_id)
                logger.info(f"Ended session due to PS5 {ps5_id} going to standby")
    
    # Update sensor states for all discovered users
    update_all_sensor_states()

def publish_user_sensors(user):
    """Publish MQTT Discovery sensors for a user"""
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
        }
    ]
    
    # Publish each sensor configuration
    for sensor in sensors:
        config_topic = f"{discovery_topic}/sensor/{sensor['unique_id']}/config"
        
        sensor_config = {
            'name': sensor['name'],
            'unique_id': sensor['unique_id'],
            'state_topic': sensor['state_topic'],
            'icon': sensor['icon'],
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
        
        # Calculate time remaining (assuming 120 min daily limit)
        daily_limit = 120  # This could be made configurable
        time_remaining = max(0, daily_limit - daily_time)
        
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
        
        logger.debug(f"Updated sensor states for {user}: daily={daily_time}, weekly={weekly_time}, monthly={monthly_time}, remaining={time_remaining}")
        
        # Log current session info for debugging
        if current_session:
            elapsed_minutes = (datetime.now() - current_session['start_time']).total_seconds() / 60
            logger.debug(f"Current session for {user}: {current_session['game']} (elapsed: {elapsed_minutes:.1f} min)")
        else:
            logger.debug(f"No active session for {user}")
        
    except Exception as e:
        logger.error(f"Failed to update sensor states for {user}: {e}")

def handle_state_change(ps5_id, data):
    """Handle PS5 state changes (on/off)"""
    if data.get('state') == 'off':
        # End any active session for this PS5
        for session_id, session in list(time_manager.active_sessions.items()):
            if session['ps5_id'] == ps5_id:
                time_manager.end_session(session_id)
                logger.info(f"Ended session due to PS5 {ps5_id} turning off")

def handle_game_change(ps5_id, data):
    """Handle game changes"""
    game = data.get('game')
    user = current_session.get('user')
    
    if game:
        logger.info(f"PS5 {ps5_id} now playing: {game}")
        
        # Check if we should start a new session
        if user and not current_session.get('start_time'):
            session_id = time_manager.start_session(user, game, ps5_id)
            current_session['start_time'] = datetime.now()
            current_session['game'] = game
        
        # Update current game in active sessions
        for session in time_manager.active_sessions.values():
            if session['ps5_id'] == ps5_id:
                session['game'] = game

def handle_user_change(ps5_id, data):
    """Handle user changes"""
    user = data.get('user') or data.get('username') or data.get('accountName')
    
    if user:
        logger.info(f"PS5 {ps5_id} user changed to: {user}")
        current_session['user'] = user
        current_session['ps5_id'] = ps5_id
        
        # Add to discovered users
        if user not in discovered_users:
            discovered_users.add(user)
            logger.info(f"Discovered new user: {user}")
        
        # Start tracking if game is known
        if current_session.get('game'):
            session_id = time_manager.start_session(user, current_session['game'], ps5_id)
            current_session['start_time'] = datetime.now()

def handle_activity_change(ps5_id, data):
    """Handle activity changes (user presence, game activity)"""
    # Extract user information from activity data
    user = data.get('user') or data.get('username') or data.get('accountName')
    game = data.get('game') or data.get('titleName')
    
    if user and user not in discovered_users:
        discovered_users.add(user)
        logger.info(f"Discovered new user from activity: {user}")
    
    if user and game:
        logger.info(f"PS5 {ps5_id} activity: {user} playing {game}")
        # Update current session
        current_session['user'] = user
        current_session['game'] = game
        current_session['ps5_id'] = ps5_id

def check_timers():
    """Background thread to check timers and enforce limits"""
    while True:
        try:
            time.sleep(60)  # Check every minute
            
            for session_id, session in list(time_manager.active_sessions.items()):
                user = session['user']
                
                # Check if limit exceeded
                if time_manager.check_limit_exceeded(user):
                    # Send warning or shutdown
                    logger.warning(f"User {user} has exceeded their time limit")
                    time_manager.add_notification(user, 'limit_exceeded', 
                        "Your time limit has been reached for today")
                    
                    # Send MQTT command to turn off PS5
                    if config.get('enable_auto_shutdown'):
                        ps5_id = session['ps5_id']
                        mqtt_client.publish(f"{config['mqtt_topic_prefix']}/{ps5_id}/command", 
                                          json.dumps({'action': 'turn_off'}))
                        
                        # End the session
                        time_manager.end_session(session_id)
                
                # Check for warning before shutdown
                elif config.get('graceful_shutdown_warnings'):
                    limit = time_manager.get_user_limit(user)
                    time_today = time_manager.get_user_time_today(user)
                    warning_minutes = config.get('warning_before_shutdown_minutes', 10)
                    
                    if limit and time_today >= (limit - warning_minutes):
                        if 'warning_sent' not in session.get('warnings_sent', []):
                            session.setdefault('warnings_sent', []).append('warning_sent')
                            logger.info(f"Sending warning to {user}")
                            time_manager.add_notification(user, 'warning', 
                                f"You have {warning_minutes} minutes remaining")
                            
        except Exception as e:
            logger.error(f"Error in timer check: {e}")

# Flask API endpoints

@app.route('/api/health', methods=['GET'])
def health_check():
    """Health check endpoint"""
    return jsonify({'status': 'ok'})

@app.route('/api/status', methods=['GET'])
def api_status():
    """Return current console status including active session details if any."""
    try:
        # Determine active session details
        active_sessions = []
        for session_id, session in list(time_manager.active_sessions.items()):
            started = session['start_time']
            elapsed_seconds = int((datetime.now() - started).total_seconds())
            active_sessions.append({
                'user': session['user'],
                'game': session['game'],
                'ps5_id': session['ps5_id'],
                'start_time': started.isoformat(),
                'elapsed_seconds': elapsed_seconds,
                'elapsed_minutes': elapsed_seconds // 60,
            })

        status = {
            'power': latest_device_status.get('power'),
            'device_status': latest_device_status.get('device_status'),
            'activity': latest_device_status.get('activity'),
            'players': latest_device_status.get('players') or [],
            'title': {
                'id': latest_device_status.get('title_id'),
                'name': latest_device_status.get('title_name'),
                'image': latest_device_status.get('title_image'),
            },
            'ps5_id': latest_device_status.get('ps5_id'),
            'last_update': latest_device_status.get('last_update'),
            'active_sessions': active_sessions,
        }
        return jsonify(status)
    except Exception as e:
        logger.error(f"/api/status error: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/users', methods=['GET'])
def get_discovered_users():
    """Get list of discovered users"""
    return jsonify({
        'users': list(discovered_users),
        'count': len(discovered_users)
    })

@app.route('/api/users/view')
def view_users():
    """View users in a simple HTML page"""
    users_html = '<br>'.join([f'• {user}' for user in discovered_users]) if discovered_users else 'No users discovered yet'
    return f'''
    <html>
    <head>
        <title>Users - PS5 Time Management</title>
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <style>
            body {{ font-family: Arial, sans-serif; margin: 40px; background: #f5f5f5; }}
            .container {{ background: white; padding: 30px; border-radius: 8px; box-shadow: 0 2px 10px rgba(0,0,0,0.1); }}
            h1 {{ color: #333; border-bottom: 2px solid #28a745; padding-bottom: 10px; }}
            .back-btn {{ display: inline-block; margin: 20px 0; padding: 10px 20px; background: #007bff; color: white; text-decoration: none; border-radius: 4px; }}
            .back-btn:hover {{ background: #0056b3; }}
            .users {{ background: #f8f9fa; padding: 15px; border-radius: 4px; margin: 20px 0; }}
        </style>
    </head>
    <body>
        <div class="container">
            <h1>📋 Discovered Users</h1>
            <div class="users">
                <strong>Users ({len(discovered_users)}):</strong><br>
                {users_html}
            </div>
            <a href="./" class="back-btn">← Back to Home</a>
        </div>
    </body>
    </html>
    '''

@app.route('/api/users/<user>/stats', methods=['GET'])
def get_user_stats_all(user):
    """Get all stats for a user"""
    if user not in discovered_users:
        return jsonify({'error': 'User not found'}), 404
    
    # Get breakdown for debugging
    daily = time_manager.get_user_time_today(user)
    weekly = time_manager.get_user_weekly_time(user)
    monthly = time_manager.get_user_monthly_time(user)
    
    # Get active session info for context
    active_session_info = []
    for session_id, session in time_manager.active_sessions.items():
        if session['user'] == user:
            elapsed = (datetime.now() - session['start_time']).total_seconds()
            active_session_info.append({
                'game': session['game'],
                'elapsed_minutes': int(elapsed / 60),
                'start_time': session['start_time'].isoformat()
            })
    
    return jsonify({
        'user': user,
        'daily': daily,
        'weekly': weekly,
        'monthly': monthly,
        'active_sessions': active_session_info,
        'top_games': time_manager.get_top_games(user, 30, 10)
    })

@app.route('/api/stats/daily/<user>', methods=['GET'])
def get_daily_stats(user):
    """Get daily stats for user"""
    minutes = time_manager.get_user_time_today(user)
    return jsonify({'user': user, 'minutes': minutes})

@app.route('/api/stats/weekly/<user>', methods=['GET'])
def get_weekly_stats(user):
    """Get weekly stats for user"""
    minutes = time_manager.get_user_weekly_time(user)
    return jsonify({'user': user, 'minutes': minutes})

@app.route('/api/stats/monthly/<user>', methods=['GET'])
def get_monthly_stats(user):
    """Get monthly stats for user"""
    minutes = time_manager.get_user_monthly_time(user)
    return jsonify({'user': user, 'minutes': minutes})

@app.route('/api/games/top/<user>', methods=['GET'])
def get_top_games(user):
    """Get top games for user"""
    days = request.args.get('days', 30, type=int)
    limit = request.args.get('limit', 10, type=int)
    
    games = time_manager.get_top_games(user, days, limit)
    return jsonify({'games': games})

@app.route('/api/limits/<user>', methods=['GET'])
def get_limit(user):
    """Get time limit for user"""
    limit = time_manager.get_user_limit(user)
    current_time = time_manager.get_user_time_today(user)
    
    return jsonify({
        'daily_limit': limit,
        'current_time': current_time,
        'remaining': limit - current_time if limit else None
    })

@app.route('/api/limits/<user>', methods=['POST'])
def set_limit(user):
    """Set time limit for user"""
    data = request.json
    daily_minutes = data.get('daily_minutes')
    enabled = data.get('enabled', True)
    
    time_manager.set_user_limit(user, daily_minutes, enabled)
    return jsonify({'status': 'success'})

@app.route('/api/active_sessions', methods=['GET'])
def get_active_sessions():
    """Get all active gaming sessions"""
    sessions = []
    for session_id, session in time_manager.active_sessions.items():
        sessions.append({
            'session_id': session_id,
            'user': session['user'],
            'game': session['game'],
            'start_time': session['start_time'].isoformat(),
            'ps5_id': session['ps5_id']
        })
    
    return jsonify({'sessions': sessions})

@app.route('/api/notifications/<user>', methods=['GET'])
def get_notifications(user):
    """Get notifications for user"""
    conn = sqlite3.connect(time_manager.db_path)
    c = conn.cursor()
    
    c.execute('''SELECT id, type, message, timestamp 
                 FROM notifications 
                 WHERE user=? AND read=0 
                 ORDER BY timestamp DESC''',
             (user,))
    
    results = c.fetchall()
    conn.close()
    
    notifications = []
    for row in results:
        notifications.append({
            'id': row[0],
            'type': row[1],
            'message': row[2],
            'timestamp': row[3]
        })
    
    return jsonify({'notifications': notifications})

@app.route('/api/debug/<user>', methods=['GET'])
def debug_user_data(user):
    """Debug endpoint to inspect user data"""
    try:
        conn = sqlite3.connect(time_manager.db_path)
        c = conn.cursor()
        
        # Get all user_stats for this user
        c.execute('''SELECT date, total_minutes, session_count 
                     FROM user_stats 
                     WHERE user=? 
                     ORDER BY date DESC''',
                 (user,))
        
        user_stats = []
        for row in c.fetchall():
            user_stats.append({
                'date': row[0],
                'minutes': row[1],
                'sessions': row[2]
            })
        
        # Get all sessions for this user
        c.execute('''SELECT start_time, end_time, duration_seconds, game 
                     FROM sessions 
                     WHERE user=? 
                     ORDER BY start_time DESC''',
                 (user,))
        
        sessions = []
        for row in c.fetchall():
            sessions.append({
                'start_time': row[0],
                'end_time': row[1],
                'duration_seconds': row[2],
                'game': row[3]
            })
        
        # Get active sessions
        active_sessions = []
        for session_id, session in time_manager.active_sessions.items():
            if session['user'] == user:
                active_sessions.append({
                    'session_id': session_id,
                    'start_time': session['start_time'].isoformat(),
                    'game': session['game'],
                    'ps5_id': session['ps5_id']
                })
        
        # Calculate current time periods
        today = datetime.now().date()
        week_start = today - timedelta(days=today.weekday())
        month_start = today.replace(day=1)
        
        conn.close()
        
        return jsonify({
            'user': user,
            'debug_info': {
                'today': today.isoformat(),
                'week_start': week_start.isoformat(),
                'month_start': month_start.isoformat(),
                'current_time': datetime.now().isoformat()
            },
            'user_stats': user_stats,
            'sessions': sessions,
            'active_sessions': active_sessions,
            'calculated_times': {
                'daily': time_manager.get_user_time_today(user),
                'weekly': time_manager.get_user_weekly_time(user),
                'monthly': time_manager.get_user_monthly_time(user)
            }
        })
    except Exception as e:
        logger.error(f"Debug endpoint error: {e}")
        return jsonify({
            'error': str(e),
            'user': user,
            'message': 'Debug endpoint failed'
        }), 500

@app.route('/api/cleanup/<user>', methods=['POST'])
def cleanup_user_data(user):
    """Clean up old test data for a user"""
    conn = sqlite3.connect(time_manager.db_path)
    c = conn.cursor()
    
    # Delete all user_stats for this user
    c.execute('DELETE FROM user_stats WHERE user=?', (user,))
    
    # Delete all sessions for this user
    c.execute('DELETE FROM sessions WHERE user=?', (user,))
    
    # Delete all game_stats for this user
    c.execute('DELETE FROM game_stats WHERE user=?', (user,))
    
    conn.commit()
    conn.close()
    
    # Force update sensor states to reflect clean data
    update_user_sensor_states(user)
    
    logger.info(f"Cleaned up all data for user {user} and updated sensor states")
    
    return jsonify({
        'message': f'Cleaned up all data for user {user} and updated sensor states',
        'user': user
    })

@app.route('/api/refresh/<user>', methods=['POST'])
def refresh_user_sensors(user):
    """Manually refresh sensor states for a user"""
    try:
        update_user_sensor_states(user)
        logger.info(f"Manually refreshed sensor states for user {user}")
        
        return jsonify({
            'message': f'Refreshed sensor states for user {user}',
            'user': user,
            'current_values': {
                'daily': time_manager.get_user_time_today(user),
                'weekly': time_manager.get_user_weekly_time(user),
                'monthly': time_manager.get_user_monthly_time(user)
            }
        })
    except Exception as e:
        logger.error(f"Error refreshing sensors for {user}: {e}")
        return jsonify({
            'error': str(e),
            'message': f'Failed to refresh sensors for user {user}'
        }), 500

def clear_all_user_data():
    """Clear all historic data for all users"""
    try:
        conn = sqlite3.connect(time_manager.db_path)
        c = conn.cursor()
        
        # Get list of all users in database
        c.execute('SELECT DISTINCT user FROM user_stats')
        db_users = [row[0] for row in c.fetchall()]
        
        # Also include currently discovered users
        all_users = list(set(db_users + list(discovered_users)))
        
        # Clear data for all users
        cleared_users = []
        for user in all_users:
            # Delete all user_stats for this user
            c.execute('DELETE FROM user_stats WHERE user=?', (user,))
            
            # Delete all sessions for this user
            c.execute('DELETE FROM sessions WHERE user=?', (user,))
            
            # Delete all game_stats for this user
            c.execute('DELETE FROM game_stats WHERE user=?', (user,))
            
            cleared_users.append(user)
        
        conn.commit()
        conn.close()
        
        # Force update sensor states for all users
        update_all_sensor_states()
        
        logger.info(f"Cleared all historic data for {len(cleared_users)} users: {cleared_users}")
        return cleared_users
        
    except Exception as e:
        logger.error(f"Error clearing all user data: {e}")
        return []

@app.route('/api/report/<user>', methods=['GET'])
def get_report(user):
    """Generate comprehensive report for user"""
    days = request.args.get('days', 7, type=int)
    
    conn = sqlite3.connect(time_manager.db_path)
    c = conn.cursor()
    
    start_date = (datetime.now() - timedelta(days=days)).date()
    
    # Get daily stats
    c.execute('''SELECT date, total_minutes, session_count 
                 FROM user_stats 
                 WHERE user=? AND date >= ? 
                 ORDER BY date DESC''',
             (user, start_date))
    
    daily_stats = []
    for row in c.fetchall():
        daily_stats.append({
            'date': row[0],
            'minutes': row[1],
            'sessions': row[2]
        })
    
    # Get game breakdown
    games = time_manager.get_top_games(user, days, 20)
    
    conn.close()
    
    return jsonify({
        'user': user,
        'period_days': days,
        'daily_stats': daily_stats,
        'top_games': games,
        'total_minutes': sum(s['minutes'] for s in daily_stats)
    })

@app.route('/')
def index():
    """Serve a simple index page with links"""
    logger.info("Index route accessed")
    return '''
    <html>
    <head>
        <title>PS5 Time Management</title>
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <style>
            body { font-family: Arial, sans-serif; margin: 40px; background: #f5f5f5; }
            .container { background: white; padding: 30px; border-radius: 8px; box-shadow: 0 2px 10px rgba(0,0,0,0.1); }
            h1 { color: #333; border-bottom: 2px solid #007bff; padding-bottom: 10px; }
            .link { display: block; margin: 15px 0; padding: 10px; background: #007bff; color: white; text-decoration: none; border-radius: 4px; }
            .link:hover { background: #0056b3; }
            .api-link { background: #28a745; }
            .api-link:hover { background: #218838; }
            .status-card { display: flex; gap: 16px; align-items: center; background: #ffffff; border-radius: 8px; padding: 16px; box-shadow: 0 1px 6px rgba(0,0,0,0.08); margin: 16px 0 24px; }
            .status-info { flex: 1; }
            .status-title { font-size: 1.1em; margin: 0 0 8px 0; color: #111; }
            .status-line { margin: 6px 0; color: #333; }
            .badge { display: inline-block; padding: 4px 10px; border-radius: 999px; font-size: 0.85em; color: #fff; }
            .badge.awake { background: #28a745; }
            .badge.standby { background: #6c757d; }
            .badge.offline { background: #dc3545; }
            .game-art { width: 92px; height: 92px; border-radius: 8px; object-fit: cover; background: #eee; }
            .muted { color: #666; }
            .mono { font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", "Courier New", monospace; }
        </style>
    </head>
    <body>
        <div class="container">
            <h1>🎮 PS5 Time Management</h1>
            <p>Welcome to the PS5 Time Management add-on!</p>

            <h2>Console Status</h2>
            <div class="status-card" id="status-card">
                <img id="game-image" class="game-art" alt="Game art" src="" style="display:none;" />
                <div class="status-info">
                    <div class="status-title">Status: <span id="status-badge" class="badge standby">Loading…</span></div>
                    <div class="status-line" id="players-line" style="display:none;"></div>
                    <div class="status-line" id="game-line" style="display:none;"></div>
                    <div class="status-line muted" id="session-line" style="display:none;"></div>
                    <div class="status-line muted mono" id="session-details" style="display:none; font-size:0.85em;"></div>
                    <div class="status-line muted"><span class="mono" id="last-update">—</span></div>
                </div>
            </div>
            
            <h2>Web Interface</h2>
            <a href="./user-management" class="link">👥 User Management</a>
            <a href="./test" class="link">🧪 Test Route</a>
            
            <h2>API Endpoints</h2>
            <a href="./api/users/view" class="link api-link">📋 List Users</a>
            <a href="./api/debug/Thomas" class="link api-link">🔍 Debug User (Thomas)</a>
            <a href="./api/stats/daily/Thomas" class="link api-link">📊 Daily Stats (Thomas)</a>
        </div>
        <script>
            function fmtMins(mins){ return mins + ' min' + (mins === 1 ? '' : 's'); }
            function fetchStatus(){
                fetch('./api/status').then(r=>r.json()).then(s=>{
                    const badge = document.getElementById('status-badge');
                    const playersLine = document.getElementById('players-line');
                    const gameLine = document.getElementById('game-line');
                    const sessionLine = document.getElementById('session-line');
                    const sessionDetails = document.getElementById('session-details');
                    const gameImg = document.getElementById('game-image');
                    const lastUpdate = document.getElementById('last-update');

                    const power = (s.power||'UNKNOWN').toUpperCase();
                    badge.textContent = power;
                    badge.className = 'badge ' + (power === 'AWAKE' ? 'awake' : (power === 'STANDBY' ? 'standby' : 'offline'));
                    lastUpdate.textContent = s.last_update ? 'Last update: ' + s.last_update : 'No updates yet';

                    // Players and activity
                    if (Array.isArray(s.players) && s.players.length){
                        playersLine.style.display = '';
                        playersLine.textContent = 'Player(s): ' + s.players.join(', ');
                    } else {
                        playersLine.style.display = 'none';
                    }

                    // Game title and image
                    if (s.title && (s.title.name || s.title.image)){
                        gameLine.style.display = '';
                        gameLine.textContent = 'Game: ' + (s.title.name || 'Unknown');
                        if (s.title.image){
                            gameImg.src = s.title.image;
                            gameImg.style.display = '';
                        } else {
                            gameImg.style.display = 'none';
                        }
                    } else {
                        gameLine.style.display = 'none';
                        gameImg.style.display = 'none';
                    }

                    // Active session timing (all active sessions)
                    if (Array.isArray(s.active_sessions) && s.active_sessions.length){
                        sessionLine.style.display = '';
                        if (s.active_sessions.length === 1) {
                            // Single player
                            const a = s.active_sessions[0];
                            sessionLine.textContent = a.user + ' — Started: ' + new Date(a.start_time).toLocaleString() + ' — Active ' + fmtMins(a.elapsed_minutes);
                        } else {
                            // Multiple players - show all
                            const sessions = s.active_sessions.map(a => 
                                a.user + ' (' + fmtMins(a.elapsed_minutes) + ')'
                            ).join(', ');
                            sessionLine.textContent = 'Active Players: ' + sessions;
                            // Add info about when sessions started
                            const startTimes = s.active_sessions.map(a => 
                                a.user + ': ' + new Date(a.start_time).toLocaleString()
                            ).join(' | ');
                            if (sessionDetails) {
                                sessionDetails.style.display = '';
                                sessionDetails.textContent = startTimes;
                            }
                        }
                    } else {
                        sessionLine.style.display = 'none';
                        if (sessionDetails) sessionDetails.style.display = 'none';
                    }
                }).catch(()=>{});
            }
            fetchStatus();
            setInterval(fetchStatus, 15000);
        </script>
    </body>
    </html>
    '''

@app.route('/test')
def test():
    """Test route to verify routing works"""
    logger.info("Test route accessed")
    return '''
    <html>
    <head>
        <title>Test Route - PS5 Time Management</title>
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <style>
            body { font-family: Arial, sans-serif; margin: 40px; background: #f5f5f5; }
            .container { background: white; padding: 30px; border-radius: 8px; box-shadow: 0 2px 10px rgba(0,0,0,0.1); }
            h1 { color: #333; border-bottom: 2px solid #28a745; padding-bottom: 10px; }
            .back-btn { display: inline-block; margin: 20px 0; padding: 10px 20px; background: #007bff; color: white; text-decoration: none; border-radius: 4px; }
            .back-btn:hover { background: #0056b3; }
            .success { background: #d4edda; color: #155724; padding: 15px; border-radius: 4px; margin: 20px 0; }
        </style>
    </head>
    <body>
        <div class="container">
            <h1>🧪 Test Route</h1>
            <div class="success">
                <strong>✅ Success!</strong> Test route works! Flask routing is functioning correctly.
            </div>
            <p>This confirms that the web interface is working properly with Home Assistant's ingress system.</p>
            <a href="./" class="back-btn">← Back to Home</a>
        </div>
    </body>
    </html>
    '''

@app.route('/user-management')
def user_management():
    """Serve the user management web interface"""
    logger.info("User management route accessed")
    try:
        return render_template('user_management.html')
    except Exception as e:
        logger.error(f"Error serving user management page: {e}")
        return f"Error loading user management page: {e}", 500

def load_config():
    """Load configuration from options.json"""
    global logger
    
    config_path = '/data/options.json'
    if os.path.exists(config_path):
        with open(config_path, 'r') as f:
            config = json.load(f)
            
            # Setup logging based on config
            log_level = config.get('log_level', 'INFO')
            logger = setup_logging(log_level)
            logger.info(f"Configuration loaded from {config_path}")
            logger.debug(f"Full configuration: {json.dumps(config, indent=2)}")
            
            # Handle clear_all_stats option
            if config.get('clear_all_stats', False):
                logger.warning("Clear all stats option detected - clearing all user data")
                clear_all_user_data()
                # Reset the option to prevent repeated clearing
                config['clear_all_stats'] = False
                with open(config_path, 'w') as f:
                    json.dump(config, f, indent=2)
                logger.info("Cleared all stats and reset option")
            
            return config
    
    logger.warning(f"Configuration file not found at {config_path}, using defaults")
    return {}

def get_mqtt_config():
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

def main():
    """Main entry point"""
    global config, time_manager, mqtt_client
    
    # Load configuration
    config = load_config()
    logger.info("Configuration loaded")
    
    # Initialize time manager
    db_path = config.get('database_path', '/data/ps5_time_management.db')
    time_manager = PS5TimeManager(db_path)
    
    # Load any previously persisted users so sensors exist without waiting for MQTT
    try:
        persisted_users = time_manager.load_users()
        if persisted_users:
            for user in persisted_users:
                if user not in discovered_users:
                    discovered_users.add(user)
                    publish_user_sensors(user)
            logger.info(f"Loaded persisted users from DB: {persisted_users}")
        else:
            logger.info("No persisted users found in DB yet")
    except Exception as e:
        logger.warning(f"Failed to initialize users from DB: {e}")
    
    # Get MQTT configuration (automatic or manual)
    mqtt_config = get_mqtt_config()
    
    logger.info(f"MQTT Configuration: {mqtt_config['host']}:{mqtt_config['port']}")
    logger.debug(f"Full MQTT config: {mqtt_config}")
    
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

