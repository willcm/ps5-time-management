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
from threading import Timer
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
        
        # Shutdown events - audit log of enforced rest mode
        c.execute('''CREATE TABLE IF NOT EXISTS shutdown_events
                     (id INTEGER PRIMARY KEY AUTOINCREMENT,
                      user TEXT,
                      ps5_id TEXT,
                      reason TEXT,
                      mode TEXT,
                      created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
        
        # User access table - whether access is allowed
        c.execute('''CREATE TABLE IF NOT EXISTS user_access
                     (user TEXT PRIMARY KEY,
                      allowed BOOLEAN DEFAULT 1)''')
        
        # Users table - persist discovered users so we don't depend on live discovery
        c.execute('''CREATE TABLE IF NOT EXISTS users
                     (user TEXT PRIMARY KEY)''')

        # Game images cache table
        c.execute('''CREATE TABLE IF NOT EXISTS game_images
                     (game TEXT PRIMARY KEY,
                      filename TEXT NOT NULL,
                      last_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
        
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

    def _ensure_image_dir(self):
        images_dir = '/data/game_images'
        os.makedirs(images_dir, exist_ok=True)
        return images_dir

    def _slugify(self, text):
        safe = ''.join(ch if ch.isalnum() or ch in (' ', '-', '_') else '_' for ch in text or 'unknown')
        return '-'.join(safe.lower().split())[:120]

    def cache_game_image(self, game_name, image_url):
        if not image_url or not game_name:
            return None
        try:
            images_dir = self._ensure_image_dir()
            slug = self._slugify(game_name)
            ext = 'jpg'
            if '.png' in image_url.lower():
                ext = 'png'
            filename = f"{slug}.{ext}"
            filepath = os.path.join(images_dir, filename)

            # If already cached, update last_seen and return
            if os.path.exists(filepath):
                conn = sqlite3.connect(self.db_path)
                c = conn.cursor()
                c.execute('''INSERT INTO game_images (game, filename) VALUES (?, ?)
                             ON CONFLICT(game) DO UPDATE SET filename=excluded.filename, last_seen=CURRENT_TIMESTAMP''',
                          (game_name, filename))
                conn.commit(); conn.close()
                logger.info(f"Game cover already cached: '{game_name}' -> {filepath}")
                return filename

            # Download and save
            # Use stdlib to avoid external dependency
            req = Request(image_url, headers={'User-Agent': 'Mozilla/5.0'})
            with urlopen(req, timeout=10) as resp:
                if resp.status == 200:
                    content = resp.read()
                    with open(filepath, 'wb') as f:
                        f.write(content)
                conn = sqlite3.connect(self.db_path)
                c = conn.cursor()
                c.execute('''INSERT INTO game_images (game, filename) VALUES (?, ?) 
                             ON CONFLICT(game) DO UPDATE SET filename=excluded.filename, last_seen=CURRENT_TIMESTAMP''',
                          (game_name, filename))
                conn.commit(); conn.close()
                logger.info(f"Cached image for game '{game_name}' -> {filepath}")
                return filename
        except Exception as e:
            logger.debug(f"Failed to cache image for {game_name}: {e}")
        return None

    def get_cached_game_image(self, game_name):
        try:
            conn = sqlite3.connect(self.db_path)
            c = conn.cursor()
            c.execute('SELECT filename FROM game_images WHERE game=?', (game_name,))
            row = c.fetchone()
            conn.close()
            if row:
                filename = row[0]
                if os.path.exists(os.path.join('/data/game_images', filename)):
                    return filename
        except Exception:
            pass
        return None
    
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
        today = start_time.date().isoformat()
        
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
        today = datetime.now().date().isoformat()
        
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
        week_start_date = today - timedelta(days=today.weekday())
        week_start = week_start_date.isoformat()
        
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
                if session_date >= week_start_date:  # Only count sessions from this week
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
        month_start_date = today.replace(day=1)
        month_start = month_start_date.isoformat()
        
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
                if session_date >= month_start_date:  # Only count sessions from this month
                    elapsed = (datetime.now() - session['start_time']).total_seconds()
                    active_time += elapsed / 60  # Convert to minutes
        
        total_time = completed_time + active_time
        logger.info(f"User {user} monthly time: {completed_time} min completed (from DB) + {active_time:.1f} min active = {total_time:.1f} min total")
        return int(round(total_time))
    
    def get_top_games(self, user, days=30, limit=10):
        """Get top games played by user in the last N days, with images when available"""
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        start_date = (datetime.now() - timedelta(days=days)).date().isoformat()
        
        c.execute('''SELECT game, SUM(minutes_played) as total 
                     FROM game_stats 
                     WHERE user=? AND date >= ? 
                     GROUP BY game 
                     ORDER BY total DESC 
                     LIMIT ?''',
                 (user, start_date, limit))
        
        results = c.fetchall()
        
        # Try to get game images from cache, otherwise attempt to cache from current status
        def normalize_title(name: str) -> str:
            try:
                lowered = (name or '').lower()
                # strip common trademark chars and spaces/punct
                for ch in ['®', '™']:
                    lowered = lowered.replace(ch, '')
                return ''.join(ch for ch in lowered if ch.isalnum() or ch == ' ').strip()
            except Exception:
                return name or ''

        current_title = normalize_title(latest_device_status.get('title_name') or '') if latest_device_status else ''
        current_image = latest_device_status.get('title_image') if latest_device_status else None
        games_with_images = []
        for row in results:
            game_name = row[0]
            minutes = row[1]
            game_image = None
            cached = self.get_cached_game_image(game_name)
            if cached:
                # Verify file actually exists on disk
                try:
                    full_path = os.path.join('/data/game_images', cached)
                    if os.path.exists(full_path):
                        pass
                    else:
                        logger.warning(f"Cached cover record found but file missing: {full_path}")
                except Exception:
                    pass
                game_image = f"/images/{cached}"
            else:
                # Try from current status and cache it (fuzzy match)
                try:
                    if current_title and current_image:
                        normalized = normalize_title(game_name)
                        if (normalized == current_title) or (normalized in current_title) or (current_title in normalized):
                            fname = self.cache_game_image(game_name, current_image)
                            if fname:
                                game_image = f"/images/{fname}"
                except Exception:
                    pass
            
            games_with_images.append({
                'game': game_name,
                'minutes': minutes,
                'image': game_image
            })
        
        conn.close()
        return games_with_images
    
    def get_game_time_today(self, user, game):
        """Get time played for a specific game today (including active sessions)"""
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        today = datetime.now().date().isoformat()
        
        # Get completed sessions from database
        c.execute('''SELECT SUM(minutes_played) FROM game_stats 
                     WHERE user=? AND game=? AND date=?''',
                 (user, game, today))
        
        result = c.fetchone()
        completed_time = result[0] if result and result[0] is not None else 0
        conn.close()
        
        # Add time from active sessions for this game
        active_time = 0
        for session_id, session in self.active_sessions.items():
            if session['user'] == user and session['game'] == game:
                elapsed = (datetime.now() - session['start_time']).total_seconds()
                active_time += elapsed / 60
        
        total_time = completed_time + active_time
        return int(round(total_time))
    
    def get_game_time_weekly(self, user, game):
        """Get time played for a specific game this week (including active sessions)"""
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        
        # Calculate week start (Monday)
        today = datetime.now().date()
        days_since_monday = today.weekday()
        week_start = (today - timedelta(days=days_since_monday)).isoformat()
        
        # Get completed sessions from database
        c.execute('''SELECT SUM(minutes_played) FROM game_stats 
                     WHERE user=? AND game=? AND date >= ?''',
                 (user, game, week_start))
        
        result = c.fetchone()
        completed_time = result[0] if result and result[0] is not None else 0
        conn.close()
        
        # Add time from active sessions for this game
        active_time = 0
        for session_id, session in self.active_sessions.items():
            if session['user'] == user and session['game'] == game:
                elapsed = (datetime.now() - session['start_time']).total_seconds()
                active_time += elapsed / 60
        
        total_time = completed_time + active_time
        return int(round(total_time))
    
    def get_game_time_monthly(self, user, game):
        """Get time played for a specific game this month (including active sessions)"""
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        
        # Calculate month start
        today = datetime.now().date()
        month_start = today.replace(day=1).isoformat()
        
        # Get completed sessions from database
        c.execute('''SELECT SUM(minutes_played) FROM game_stats 
                     WHERE user=? AND game=? AND date >= ?''',
                 (user, game, month_start))
        
        result = c.fetchone()
        completed_time = result[0] if result and result[0] is not None else 0
        conn.close()
        
        # Add time from active sessions for this game
        active_time = 0
        for session_id, session in self.active_sessions.items():
            if session['user'] == user and session['game'] == game:
                elapsed = (datetime.now() - session['start_time']).total_seconds()
                active_time += elapsed / 60
        
        total_time = completed_time + active_time
        return int(round(total_time))
    
    def get_all_games_stats(self, user):
        """Get stats for all games played by user, organized by period"""
        # Get all unique games for this user
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        
        c.execute('''SELECT DISTINCT game FROM game_stats WHERE user=? 
                     UNION 
                     SELECT DISTINCT game FROM sessions WHERE user=?''',
                 (user, user))
        
        games = [row[0] for row in c.fetchall()]
        conn.close()
        
        # Add games from active sessions
        for session_id, session in self.active_sessions.items():
            if session['user'] == user and session['game'] not in games:
                games.append(session['game'])
        
        # Get stats for each game
        game_stats = {}
        for game in games:
            game_stats[game] = {
                'daily': self.get_game_time_today(user, game),
                'weekly': self.get_game_time_weekly(user, game),
                'monthly': self.get_game_time_monthly(user, game)
            }
        
        return game_stats
    
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

    def get_user_access(self, user):
        """Return whether the specified user's access is allowed (default True)."""
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        c.execute('SELECT allowed FROM user_access WHERE user=?', (user,))
        row = c.fetchone()
        conn.close()
        if row is None:
            return True
        return bool(row[0])

    def set_user_access(self, user, allowed):
        """Set access allowed flag for a user."""
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        c.execute('''INSERT INTO user_access (user, allowed)
                     VALUES (?, ?)
                     ON CONFLICT(user) DO UPDATE SET allowed=excluded.allowed''',
                  (user, 1 if allowed else 0))
        conn.commit()
        conn.close()
        logger.info(f"Access for user {user} set to {'allowed' if allowed else 'blocked'}")
    
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
        
        # Subscribe to ps5-mqtt topics
        topic_prefix = config.get('mqtt_topic_prefix', 'ps5-mqtt')
        subscribe_topic = f"{topic_prefix}/#"
        logger.info(f"Subscribing to MQTT topic: {subscribe_topic}")
        client.subscribe(subscribe_topic)
        
        logger.info(f"Subscribed to MQTT topics with prefix: {topic_prefix}")
        # Publish discovery for all known users now that we're connected
        try:
            if discovered_users:
                for user in list(discovered_users):
                    publish_user_sensors(user)
            # Immediately publish current states so entities have retained values
            update_all_sensor_states()
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
        logger.info(f"Parsed MQTT data: {data}")
        
        if len(parts) >= 2:
            ps5_id = parts[1]
            logger.info(f"Extracted PS5 ID: {ps5_id}")
            
            # Handle the main ps5-mqtt/{device_id} topic which contains all device info
            if len(parts) == 2 and parts[0] == 'ps5-mqtt':
                logger.info(f"Processing as device update for PS5 {ps5_id}")
                handle_device_update(ps5_id, data)
            else:
                logger.debug(f"Ignoring non-device topic: {parts}")
                
    except json.JSONDecodeError:
        logger.error(f"Failed to parse JSON from topic {topic}, payload: {payload}")
    except Exception as e:
        logger.error(f"Error handling MQTT message: {e}")

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
                       latest_device_status, debug_user_name)

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
    
    # Handle clear_all_stats option
    if config_dict.get('clear_all_stats', False):
        logger.warning("Clear all stats option detected - clearing all user data")
        clear_all_user_data()
        # Reset the option to prevent repeated clearing
        config_dict['clear_all_stats'] = False
        config_path = '/data/options.json'
        with open(config_path, 'w') as f:
            json.dump(config_dict, f, indent=2)
        logger.info("Cleared all stats and reset option")
    
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

