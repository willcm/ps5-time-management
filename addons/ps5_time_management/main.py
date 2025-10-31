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

# Import from mqtt.discovery module
from mqtt.discovery import discover_users_from_ps5_mqtt as _discover_users_from_ps5_mqtt

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

def on_connect(client, userdata, flags, reason_code, properties):
    """Callback when connected to MQTT broker"""
    global mqtt_connected
    mqtt_connected = True
    logger.info(f"MQTT on_connect callback: reason_code={reason_code}, flags={flags}")
    
    # Update shutdown manager with connected client
    set_shutdown_dependencies(time_manager, mqtt_client, True, config)
    
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
                # Policy: immediate enforcement if manual override or limit==0.
                # Otherwise, if a warning is active due to time elapsed, let the countdown continue.
                try:
                    # Manual override path
                    if not time_manager.get_user_access(player):
                        logger.warning(f"Access disabled for {player}; applying shutdown policy")
                        apply_shutdown_policy(player, ps5_id, reason='access_disabled')
                        continue
                    # Daily limit exhausted now (including zero)
                    lim = time_manager.get_user_limit(player)
                    if lim is not None:
                        current = time_manager.get_user_time_today(player)
                        if current >= lim:
                            logger.warning(f"Daily limit reached for {player}; applying shutdown policy")
                            apply_shutdown_policy(player, ps5_id, reason='limit_reached')
                            continue
                except Exception:
                    pass
                # Attempt to cache game image proactively
                try:
                    if data.get('title_image') and game_name:
                        time_manager.cache_game_image(game_name, data.get('title_image'))
                except Exception:
                    pass
                # Enforce access allowed toggle
                if not time_manager.get_user_access(player):
                    logger.warning(f"Access blocked for {player}; enforcing action")
                    # Trigger warning then shutdown instead of immediate
                    try:
                        start_shutdown_warning(player, ps5_id)
                    except Exception as e:
                        logger.error(f"Failed to start warning for {player}: {e}")
                    continue

                session_id = time_manager.start_session(player, game_name, ps5_id)
                if session_id:
                    if debug_user_name and debug_user_name == player:
                        logger.info(f"[DEBUG:{player}] Session started (ID: {session_id}) for game {game_name}")
                    else:
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
    
    # Update sensor states for all discovered users (only if MQTT is ready)
    if mqtt_connected and mqtt_client is not None:
        update_all_sensor_states()

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
    _check_timers(time_manager, config, apply_shutdown_policy)

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

@app.route('/images/<path:filename>')
def serve_cached_image(filename):
    """Serve cached game images from /data/game_images"""
    try:
        directory = '/data/game_images'
        full_path = os.path.join(directory, filename)
        if os.path.exists(full_path):
            logger.debug(f"Serving cached image: {full_path}")
            return send_from_directory(directory, filename)
        else:
            logger.warning(f"Requested image not found on disk: {full_path}")
            return "", 404
    except Exception as e:
        logger.error(f"Image serve error for {filename}: {e}")
        return "", 404

@app.route('/stats/<user>/image/<path:filename>')
def serve_stats_scoped_image(user, filename):
    """Ingress-safe image URL under the stats namespace; proxies to cached image server."""
    try:
        return serve_cached_image(filename)
    except Exception as e:
        logger.error(f"Stats-scoped image serve error for {filename}: {e}")
        return "", 404

@app.route('/ps5.svg')
def serve_ps5_svg():
    """Serve the PS5 SVG icon"""
    try:
        svg_path = os.path.join('/app', 'ps5.svg')
        if os.path.exists(svg_path):
            with open(svg_path, 'r', encoding='utf-8') as f:
                return f.read(), 200, {'Content-Type': 'image/svg+xml'}
        else:
            return "", 404
    except Exception as e:
        logger.error(f"SVG serve error: {e}")
        return "", 404

# Removed debug endpoint /api/log_image_error

@app.route('/api/images', methods=['GET'])
def list_cached_images():
    """List cached image filenames under /data/game_images."""
    try:
        directory = '/data/game_images'
        if not os.path.isdir(directory):
            logger.info(f"Cache directory missing: {directory}")
            return jsonify({'images': [], 'count': 0})
        files = []
        for name in os.listdir(directory):
            full = os.path.join(directory, name)
            if os.path.isfile(full):
                files.append(name)
        files.sort()
        logger.debug(f"Cached images listed: {len(files)} files")
        return jsonify({'images': files, 'count': len(files)})
    except Exception as e:
        logger.error(f"Failed to list cached images: {e}")
        return jsonify({'error': str(e)}), 500
@app.route('/api/users', methods=['GET'])
def get_discovered_users():
    """Get list of discovered users"""
    return jsonify({
        'users': list(discovered_users),
        'count': len(discovered_users)
    })

@app.route('/api/access/<user>', methods=['GET', 'POST'])
def user_access(user):
    """Get or set access allowed for a user."""
    if request.method == 'GET':
        allowed = time_manager.get_user_access(user)
        return jsonify({'user': user, 'allowed': allowed})
    else:
        try:
            data = request.get_json(force=True) or {}
            allowed = bool(data.get('allowed', True))
            time_manager.set_user_access(user, allowed)
            return jsonify({'user': user, 'allowed': allowed})
        except Exception as e:
            logger.error(f"Failed to update access for {user}: {e}")
            return jsonify({'error': str(e)}), 400

@app.route('/api/debug_user', methods=['GET', 'POST'])
def api_debug_user():
    """Get or set the per-user debug filter."""
    global debug_user_name
    if request.method == 'GET':
        return jsonify({'debug_user': debug_user_name})
    try:
        data = request.get_json(force=True) or {}
        debug_user_name = data.get('debug_user') or None
        logger.info(f"Per-user debug set to: {debug_user_name}")
        return jsonify({'debug_user': debug_user_name})
    except Exception as e:
        return jsonify({'error': str(e)}), 400

@app.route('/api/republish_discovery', methods=['POST'])
def api_republish_all_discovery():
    """Republish MQTT discovery for all known users (useful after updates)."""
    if not mqtt_connected or mqtt_client is None:
        return jsonify({'error': 'MQTT not connected'}), 503
    count = 0
    for user in list(discovered_users):
        try:
            publish_user_sensors(user)
            count += 1
        except Exception as e:
            logger.warning(f"Failed to republish discovery for {user}: {e}")
    return jsonify({'republished': count, 'users': list(discovered_users)})

@app.route('/api/republish_discovery/<user>', methods=['POST'])
def api_republish_user_discovery(user):
    if not mqtt_connected or mqtt_client is None:
        return jsonify({'error': 'MQTT not connected'}), 503
    if user not in discovered_users:
        return jsonify({'error': 'User not found'}), 404
    try:
        publish_user_sensors(user)
        return jsonify({'republished': 1, 'user': user})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

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
        'top_games': time_manager.get_top_games(user, 30, 10),
        'games': time_manager.get_all_games_stats(user)  # Per-game breakdown by period
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

@app.route('/api/games/<user>', methods=['GET'])
def get_user_games_stats(user):
    """Get stats for all games played by a user (daily/weekly/monthly breakdown)"""
    if user not in discovered_users:
        return jsonify({'error': 'User not found'}), 404
    
    games_stats = time_manager.get_all_games_stats(user)
    return jsonify({
        'user': user,
        'games': games_stats
    })

@app.route('/api/shutdown_events', methods=['GET'])
def api_shutdown_events():
    """Return recent shutdown events (last 50)."""
    try:
        conn = sqlite3.connect(time_manager.db_path)
        c = conn.cursor()
        c.execute('''SELECT user, ps5_id, reason, mode, created_at
                     FROM shutdown_events
                     ORDER BY created_at DESC
                     LIMIT 50''')
        rows = [
            { 'user': r[0], 'ps5_id': r[1], 'reason': r[2], 'mode': r[3], 'created_at': r[4] }
            for r in c.fetchall()
        ]
        conn.close()
        return jsonify({'events': rows})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/games/<user>/<game>', methods=['GET'])
def get_game_stats(user, game):
    """Get stats for a specific game for a user (daily/weekly/monthly)"""
    if user not in discovered_users:
        return jsonify({'error': 'User not found'}), 404
    
    return jsonify({
        'user': user,
        'game': game,
        'daily': time_manager.get_game_time_today(user, game),
        'weekly': time_manager.get_game_time_weekly(user, game),
        'monthly': time_manager.get_game_time_monthly(user, game)
    })

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
    return _clear_all_user_data(time_manager, discovered_users, update_all_sensor_states)

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
    """Serve Tailwind-based index page"""
    logger.info("Index route accessed")
    return render_template('index.html')

@app.route('/globals.css')
def globals_css():
    try:
        return send_from_directory('templates', 'globals.css')
    except Exception as e:
        logger.error(f"Failed to serve globals.css: {e}")
        return '', 404

@app.route('/stats/<user>')
def user_stats_page(user):
    """Serve the detailed stats page for a user"""
    logger.info(f"Stats page accessed for user: {user}")
    if user not in discovered_users:
        return f"User '{user}' not found", 404
    
    try:
        # Get all stats data
        stats_data = {
            'user': user,
            'daily': time_manager.get_user_time_today(user),
            'weekly': time_manager.get_user_weekly_time(user),
            'monthly': time_manager.get_user_monthly_time(user),
            'top_games': time_manager.get_top_games(user, 30, 20),  # Top 20 games
            'games': time_manager.get_all_games_stats(user)
        }
        
        # Get active sessions info
        active_sessions_info = []
        for session_id, session in time_manager.active_sessions.items():
            if session['user'] == user:
                elapsed = (datetime.now() - session['start_time']).total_seconds()
                active_sessions_info.append({
                    'game': session['game'],
                    'elapsed_minutes': int(elapsed / 60),
                    'start_time': session['start_time'].isoformat()
                })
        stats_data['active_sessions'] = active_sessions_info
        
        return render_template('user_stats.html', **stats_data)
    except Exception as e:
        logger.error(f"Error serving stats page for {user}: {e}")
        return f"Error loading stats page: {e}", 500

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

