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
from flask import Flask, jsonify, request
from flask_cors import CORS
import logging

# Configure logging - will be updated after config is loaded
def setup_logging(log_level='INFO'):
    """Setup logging with configurable level"""
    level = getattr(logging, log_level.upper(), logging.INFO)
    
    logging.basicConfig(
        level=level,
        format='%(asctime)s - %(name)s - %(levelname)s - [%(filename)s:%(lineno)d] - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    
    # Also log to console
    console_handler = logging.StreamHandler()
    console_handler.setLevel(level)
    console_handler.setFormatter(logging.Formatter(
        '%(asctime)s - %(levelname)s - %(message)s',
        datefmt='%H:%M:%S'
    ))
    
    root_logger = logging.getLogger()
    root_logger.addHandler(console_handler)
    
    return logging.getLogger(__name__)

# Create logger - will be reconfigured with proper level after config load
logger = setup_logging()

# Initialize Flask app
app = Flask(__name__)
CORS(app)

# Configuration
config = {}
mqtt_client = None
discovered_users = set()  # Set of discovered usernames
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
        
        conn.commit()
        conn.close()
        logger.info("Database initialized successfully")
    
    def start_session(self, user, game, ps5_id):
        """Start a new gaming session"""
        if user in self.active_sessions:
            logger.warning(f"User {user} already has an active session")
            return False
        
        session_id = f"{user}_{int(time.time())}"
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
        
        # Update daily stats
        today = start_time.date()
        c.execute('''INSERT OR REPLACE INTO user_stats 
                     (user, date, total_minutes, session_count)
                     VALUES (?, ?, 
                        COALESCE((SELECT total_minutes FROM user_stats WHERE user=? AND date=?), 0) + ?,
                        COALESCE((SELECT session_count FROM user_stats WHERE user=? AND date=?), 0) + 1)''',
                 (user, today, user, today, int(duration/60), user, today))
        
        # Update game stats
        c.execute('''INSERT OR REPLACE INTO game_stats 
                     (user, game, date, minutes_played)
                     VALUES (?, ?, ?,
                        COALESCE((SELECT minutes_played FROM game_stats WHERE user=? AND game=? AND date=?), 0) + ?)''',
                 (user, game, today, user, game, today, int(duration/60)))
        
        conn.commit()
        conn.close()
        
        logger.info(f"Ended session for user {user} playing {game} ({int(duration/60)} minutes)")
        return True
    
    def get_user_time_today(self, user):
        """Get total time played today by user"""
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        today = datetime.now().date()
        
        c.execute('''SELECT total_minutes FROM user_stats 
                     WHERE user=? AND date=?''',
                 (user, today))
        
        result = c.fetchone()
        conn.close()
        
        return result[0] if result else 0
    
    def get_user_weekly_time(self, user):
        """Get total time played this week by user"""
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        week_start = datetime.now().date() - timedelta(days=datetime.now().weekday())
        
        c.execute('''SELECT SUM(total_minutes) FROM user_stats 
                     WHERE user=? AND date >= ?''',
                 (user, week_start))
        
        result = c.fetchone()
        conn.close()
        
        return result[0] if result else 0
    
    def get_user_monthly_time(self, user):
        """Get total time played this month by user"""
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        month_start = datetime.now().replace(day=1).date()
        
        c.execute('''SELECT SUM(total_minutes) FROM user_stats 
                     WHERE user=? AND date >= ?''',
                 (user, month_start))
        
        result = c.fetchone()
        conn.close()
        
        return result[0] if result else 0
    
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

def on_connect(client, userdata, flags, rc):
    """Callback when connected to MQTT broker"""
    if rc == 0:
        logger.info("Connected to MQTT broker successfully")
        
        # Discover users from ps5-mqtt configuration
        discover_users_from_ps5_mqtt()
        
        # Subscribe to ps5-mqtt topics
        topic_prefix = config.get('mqtt_topic_prefix', 'ps5')
        client.subscribe(f"{topic_prefix}/+/state")
        client.subscribe(f"{topic_prefix}/+/game")
        client.subscribe(f"{topic_prefix}/+/user")
        client.subscribe(f"{topic_prefix}/+/status")
        client.subscribe(f"{topic_prefix}/+/activity")  # Additional topic for user activity
        
        # Subscribe to all topics to discover users dynamically
        client.subscribe(f"{topic_prefix}/+/+")
        
        logger.info(f"Subscribed to MQTT topics with prefix: {topic_prefix}")
    else:
        logger.error(f"Failed to connect to MQTT broker with code {rc}")

def on_message(client, userdata, msg):
    """Callback when message received from MQTT broker"""
    topic = msg.topic
    payload = msg.payload.decode('utf-8')
    
    try:
        data = json.loads(payload)
        logger.debug(f"Received MQTT message on {topic}: {data}")
        
        # Parse topic to get PS5 ID
        parts = topic.split('/')
        if len(parts) >= 2:
            ps5_id = parts[1]
            
            # Discover users from MQTT messages
            if 'user' in topic and isinstance(data, dict):
                username = data.get('user') or data.get('username') or data.get('accountName')
                if username and username not in discovered_users:
                    discovered_users.add(username)
                    logger.info(f"Discovered new user from MQTT: {username}")
            
            # Handle different message types
            if 'state' in topic:
                handle_state_change(ps5_id, data)
            elif 'game' in topic:
                handle_game_change(ps5_id, data)
            elif 'user' in topic:
                handle_user_change(ps5_id, data)
            elif 'activity' in topic:
                handle_activity_change(ps5_id, data)
                
    except json.JSONDecodeError:
        logger.error(f"Failed to parse JSON from topic {topic}")
    except Exception as e:
        logger.error(f"Error handling MQTT message: {e}")

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

@app.route('/api/users', methods=['GET'])
def get_discovered_users():
    """Get list of discovered users"""
    return jsonify({
        'users': list(discovered_users),
        'count': len(discovered_users)
    })

@app.route('/api/users/<user>/stats', methods=['GET'])
def get_user_stats_all(user):
    """Get all stats for a user"""
    if user not in discovered_users:
        return jsonify({'error': 'User not found'}), 404
    
    daily = time_manager.get_user_time_today(user)
    weekly = time_manager.get_user_weekly_time(user)
    monthly = time_manager.get_user_monthly_time(user)
    
    return jsonify({
        'user': user,
        'daily': daily,
        'weekly': weekly,
        'monthly': monthly,
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
            
            return config
    
    logger.warning(f"Configuration file not found at {config_path}, using defaults")
    return {}

def get_mqtt_config():
    """Get MQTT configuration from Home Assistant or manual config"""
    # Check for Home Assistant MQTT service configuration
    ha_mqtt_config = {
        'host': os.environ.get('MQTT_HOST'),
        'port': int(os.environ.get('MQTT_PORT', 1883)),
        'user': os.environ.get('MQTT_USERNAME'),
        'password': os.environ.get('MQTT_PASSWORD'),
        'discovery_topic': os.environ.get('DISCOVERY_TOPIC', 'homeassistant')
    }
    
    # If Home Assistant provided MQTT config, use it
    if ha_mqtt_config['host']:
        logger.info("Using Home Assistant MQTT service configuration")
        return ha_mqtt_config
    
    # Fall back to manual configuration
    mqtt_config = config.get('mqtt', {})
    manual_config = {
        'host': mqtt_config.get('host', 'core-mosquitto'),
        'port': int(mqtt_config.get('port', 1883)),
        'user': mqtt_config.get('user', ''),
        'password': mqtt_config.get('pass', ''),
        'discovery_topic': mqtt_config.get('discovery_topic', 'homeassistant')
    }
    
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
    
    # Get MQTT configuration (automatic or manual)
    mqtt_config = get_mqtt_config()
    
    logger.info(f"MQTT Configuration: {mqtt_config['host']}:{mqtt_config['port']}")
    logger.debug(f"Full MQTT config: {mqtt_config}")
    
    # Set up MQTT client
    mqtt_client = mqtt.Client(client_id="ps5_time_management", callback_api_version=mqtt.CallbackAPIVersion.VERSION1)
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
    
    # Start Flask app
    port = int(os.environ.get('PORT', 8080))
    logger.info(f"Starting Flask app on port {port}")
    app.run(host='0.0.0.0', port=port, threaded=True)

if __name__ == '__main__':
    main()

