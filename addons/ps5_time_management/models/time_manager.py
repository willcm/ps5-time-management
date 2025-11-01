"""PS5TimeManager class for managing gaming sessions and statistics"""
import os
import sqlite3
import time
import logging
from datetime import datetime, timedelta
from urllib.request import Request, urlopen

logger = logging.getLogger(__name__)

# This will be set by main.py via set_dependencies
latest_device_status = {}


def set_latest_device_status(status):
    """Set the latest device status for game image caching"""
    global latest_device_status
    latest_device_status = status


class PS5TimeManager:
    def __init__(self, db_path, ha_client=None, use_ha_history=True):
        """Initialize PS5TimeManager
        
        Args:
            db_path: Path to SQLite database
            ha_client: HomeAssistantClient instance (optional)
            use_ha_history: Whether to use HA history as source of truth
        """
        self.db_path = db_path
        self.init_database()
        self.active_sessions = {}
        self.user_limits = {}
        self.timer_thread = None
        self.ha_client = ha_client
        self.use_ha_history = use_ha_history and ha_client is not None
        self._ha_available = None  # Cache HA availability check
    
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
        
        now = datetime.now()
        session_id = f"{ps5_id}:{user}:{int(time.time())}"
        self.active_sessions[session_id] = {
            'user': user,
            'game': game,
            'start_time': now,
            'ps5_id': ps5_id,
            'warnings_sent': [],
            'last_update': now  # Track last MQTT update for timeout detection
        }
        
        logger.info(f"Started session for user {user} playing {game}")
        return session_id
    
    def update_session_heartbeat(self, user, ps5_id):
        """Update the last_update timestamp for active sessions (called on MQTT updates)"""
        now = datetime.now()
        for session_id, session in self.active_sessions.items():
            if session['user'] == user and session.get('ps5_id') == ps5_id:
                session['last_update'] = now
                logger.debug(f"Updated heartbeat for session {session_id}")

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
        """End a gaming session and save minimal session record to database
        
        Note: Time stats are now tracked in Home Assistant history, not SQLite.
        We only save session records for reference/backup purposes.
        """
        if session_id not in self.active_sessions:
            logger.warning(f"Session {session_id} not found")
            return False
        
        session = self.active_sessions.pop(session_id)
        user = session['user']
        game = session['game']
        start_time = session['start_time']
        end_time = datetime.now()
        duration = (end_time - start_time).total_seconds()
        
        # Save minimal session record to database (for reference only)
        # Stats are tracked in HA history, not SQLite
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        
        c.execute('''INSERT INTO sessions 
                     (user, game, start_time, end_time, duration_seconds, ps5_id)
                     VALUES (?, ?, ?, ?, ?, ?)''',
                 (user, game, start_time, end_time, int(duration), session['ps5_id']))
        
        conn.commit()
        conn.close()
        
        logger.info(f"Ended session for user {user} playing {game} ({int(duration/60)} minutes) - stats tracked in HA history")
        return True
    
    def _is_ha_available(self):
        """Check if HA API is available (with caching)"""
        if not self.use_ha_history or not self.ha_client:
            return False
        
        if self._ha_available is None:
            self._ha_available = self.ha_client.is_available()
            if self._ha_available:
                logger.debug("HA API is available, using HA history for time calculations")
            else:
                logger.info("HA API not available, falling back to SQLite for time calculations")
        
        return self._ha_available
    
    def get_user_time_today(self, user):
        """Get total time played today by user
        
        Uses HA history if available, otherwise falls back to SQLite.
        """
        today_date = datetime.now().date()
        
        # Try HA history first (single source of truth)
        if self._is_ha_available():
            try:
                from ha.history import get_daily_time_from_ha
                ha_time = get_daily_time_from_ha(self.ha_client, user, today_date)
                logger.debug(f"User {user} time today from HA: {ha_time:.1f} min")
                
                # Add active session time (HA only tracks completed sessions)
                active_time = 0
                for session_id, session in self.active_sessions.items():
                    if session['user'] == user:
                        session_start = session['start_time']
                        now = datetime.now()
                        
                        # Only count time from today
                        if session_start.date() == today_date:
                            elapsed = (now - session_start).total_seconds()
                            active_time += elapsed / 60
                        else:
                            # Session started yesterday - count from midnight
                            today_start = datetime.combine(today_date, datetime.min.time())
                            elapsed = (now - today_start).total_seconds()
                            active_time += elapsed / 60
                
                total_time = ha_time + active_time
                logger.info(f"User {user} time today: {ha_time:.1f} min (HA) + {active_time:.1f} min active = {total_time:.1f} min total")
                # Ensure we don't return 0 if HA history exists but calculation might be incomplete
                # If we have HA history but total is 0, and there's an active session, use active time
                if ha_time == 0 and active_time > 0:
                    logger.debug(f"HA returned 0 but active session exists - using active time: {active_time:.1f} min")
                return max(0, int(round(total_time)))
            except Exception as e:
                logger.warning(f"Failed to get time from HA, falling back to SQLite: {e}")
        
        # Fallback to SQLite
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        today = today_date.isoformat()
        
        # Get completed sessions from database
        c.execute('''SELECT total_minutes FROM user_stats 
                     WHERE user=? AND date=?''',
                 (user, today))
        
        result = c.fetchone()
        completed_time = result[0] if result and result[0] is not None else 0
        conn.close()
        
        # Add time from active sessions (only count today's portion)
        active_time = 0
        active_count = 0
        today_start = datetime.combine(today_date, datetime.min.time())
        
        for session_id, session in self.active_sessions.items():
            if session['user'] == user:
                session_start = session['start_time']
                now = datetime.now()
                
                # Only count time from today (handle sessions that started yesterday)
                if session_start.date() == today_date:
                    # Session started today - count all elapsed time
                    elapsed = (now - session_start).total_seconds()
                    session_minutes = elapsed / 60
                else:
                    # Session started before today - only count time since midnight
                    elapsed = (now - today_start).total_seconds()
                    session_minutes = elapsed / 60
                
                active_time += session_minutes
                active_count += 1
                logger.debug(f"Active session for {user}: {session['game']} - {session_minutes:.1f} minutes elapsed today")
        
        total_time = completed_time + active_time
        logger.info(f"User {user} time today (SQLite): {completed_time} min completed + {active_time:.1f} min active ({active_count} sessions) = {total_time:.1f} min total")
        return int(round(total_time))  # Round instead of truncate for better accuracy
    
    def get_user_weekly_time(self, user):
        """Get total time played this week by user
        
        Uses HA history if available, otherwise falls back to SQLite.
        """
        # Calculate week start (Monday)
        today = datetime.now().date()
        week_start_date = today - timedelta(days=today.weekday())
        
        # Try HA history first (single source of truth)
        if self._is_ha_available():
            try:
                from ha.history import get_weekly_time_from_ha
                ha_time = get_weekly_time_from_ha(self.ha_client, user, week_start_date)
                logger.debug(f"User {user} weekly time from HA: {ha_time:.1f} min")
                
                # Add active session time
                active_time = 0
                for session_id, session in self.active_sessions.items():
                    if session['user'] == user:
                        session_date = session['start_time'].date()
                        if session_date >= week_start_date:
                            elapsed = (datetime.now() - session['start_time']).total_seconds()
                            active_time += elapsed / 60
                
                total_time = ha_time + active_time
                logger.info(f"User {user} weekly time: {ha_time:.1f} min (HA) + {active_time:.1f} min active = {total_time:.1f} min total")
                return int(round(total_time))
            except Exception as e:
                logger.warning(f"Failed to get weekly time from HA, falling back to SQLite: {e}")
        
        # Fallback to SQLite
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        week_start = week_start_date.isoformat()
        
        c.execute('''SELECT SUM(total_minutes) FROM user_stats 
                     WHERE user=? AND date >= ?''',
                 (user, week_start))
        
        result = c.fetchone()
        completed_time = result[0] if result and result[0] is not None else 0
        conn.close()
        
        # Add time from active sessions
        active_time = 0
        for session_id, session in self.active_sessions.items():
            if session['user'] == user:
                session_date = session['start_time'].date()
                if session_date >= week_start_date:
                    elapsed = (datetime.now() - session['start_time']).total_seconds()
                    active_time += elapsed / 60
        
        total_time = completed_time + active_time
        logger.info(f"User {user} weekly time (SQLite): {completed_time} min completed + {active_time:.1f} min active = {total_time:.1f} min total")
        return int(round(total_time))
    
    def get_user_monthly_time(self, user):
        """Get total time played this month by user
        
        Uses HA history if available, otherwise falls back to SQLite.
        """
        # Calculate month start
        today = datetime.now().date()
        month_start_date = today.replace(day=1)
        
        # Try HA history first (single source of truth)
        if self._is_ha_available():
            try:
                from ha.history import get_monthly_time_from_ha
                year = today.year
                month = today.month
                ha_time = get_monthly_time_from_ha(self.ha_client, user, year, month)
                logger.debug(f"User {user} monthly time from HA: {ha_time:.1f} min")
                
                # Add active session time
                active_time = 0
                for session_id, session in self.active_sessions.items():
                    if session['user'] == user:
                        session_date = session['start_time'].date()
                        if session_date >= month_start_date:
                            elapsed = (datetime.now() - session['start_time']).total_seconds()
                            active_time += elapsed / 60
                
                total_time = ha_time + active_time
                logger.info(f"User {user} monthly time: {ha_time:.1f} min (HA) + {active_time:.1f} min active = {total_time:.1f} min total")
                return int(round(total_time))
            except Exception as e:
                logger.warning(f"Failed to get monthly time from HA, falling back to SQLite: {e}")
        
        # Fallback to SQLite
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        month_start = month_start_date.isoformat()
        
        c.execute('''SELECT SUM(total_minutes) FROM user_stats 
                     WHERE user=? AND date >= ?''',
                 (user, month_start))
        
        result = c.fetchone()
        completed_time = result[0] if result and result[0] is not None else 0
        conn.close()
        
        # Add time from active sessions
        active_time = 0
        for session_id, session in self.active_sessions.items():
            if session['user'] == user:
                session_date = session['start_time'].date()
                if session_date >= month_start_date:
                    elapsed = (datetime.now() - session['start_time']).total_seconds()
                    active_time += elapsed / 60
        
        total_time = completed_time + active_time
        logger.info(f"User {user} monthly time (SQLite): {completed_time} min completed + {active_time:.1f} min active = {total_time:.1f} min total")
        return int(round(total_time))
    
    def get_top_games(self, user, days=30, limit=10):
        """Get top games played by user in the last N days, with images when available
        
        Uses HA history if available, otherwise falls back to SQLite.
        """
        start_date = (datetime.now() - timedelta(days=days)).date()
        end_date = datetime.now().date()
        
        # Helper function for image handling
        def normalize_title(name: str) -> str:
            try:
                lowered = (name or '').lower()
                for ch in ['®', '™']:
                    lowered = lowered.replace(ch, '')
                return ''.join(ch for ch in lowered if ch.isalnum() or ch == ' ').strip()
            except Exception:
                return name or ''

        current_title = normalize_title(latest_device_status.get('title_name') or '') if latest_device_status else ''
        current_image = latest_device_status.get('title_image') if latest_device_status else None
        
        # Try HA history first (single source of truth)
        if self._is_ha_available():
            try:
                from ha.history import get_game_times_from_ha
                game_times = get_game_times_from_ha(self.ha_client, user, start_date, end_date)
                
                # Sort by time descending and limit
                sorted_games = sorted(game_times.items(), key=lambda x: x[1], reverse=True)[:limit]
                
                games_with_images = []
                for game_name, minutes in sorted_games:
                    game_image = None
                    cached = self.get_cached_game_image(game_name)
                    if cached:
                        try:
                            full_path = os.path.join('/data/game_images', cached)
                            if os.path.exists(full_path):
                                game_image = f"/images/{cached}"
                        except Exception:
                            pass
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
                        'minutes': int(round(minutes)),
                        'image': game_image
                    })
                
                return games_with_images
            except Exception as e:
                logger.warning(f"Failed to get top games from HA, falling back to SQLite: {e}")
        
        # Fallback to SQLite
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        start_date_str = start_date.isoformat()
        
        c.execute('''SELECT game, SUM(minutes_played) as total 
                     FROM game_stats 
                     WHERE user=? AND date >= ? 
                     GROUP BY game 
                     ORDER BY total DESC 
                     LIMIT ?''',
                 (user, start_date_str, limit))
        
        results = c.fetchall()
        games_with_images = []
        for row in results:
            game_name = row[0]
            minutes = row[1]
            game_image = None
            cached = self.get_cached_game_image(game_name)
            if cached:
                try:
                    full_path = os.path.join('/data/game_images', cached)
                    if os.path.exists(full_path):
                        game_image = f"/images/{cached}"
                except Exception:
                    pass
            else:
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
        """Get time played for a specific game today
        
        Uses HA history if available, otherwise falls back to SQLite.
        """
        today_date = datetime.now().date()
        
        # Try HA history first (single source of truth)
        if self._is_ha_available():
            try:
                from ha.history import get_game_times_from_ha
                game_times = get_game_times_from_ha(self.ha_client, user, today_date, today_date)
                ha_time = game_times.get(game, 0.0)
                logger.debug(f"User {user} game {game} today from HA: {ha_time:.1f} min")
                
                # Add time from active sessions for this game
                active_time = 0
                for session_id, session in self.active_sessions.items():
                    if session['user'] == user and session['game'] == game:
                        elapsed = (datetime.now() - session['start_time']).total_seconds()
                        active_time += elapsed / 60
                
                total_time = ha_time + active_time
                return int(round(total_time))
            except Exception as e:
                logger.warning(f"Failed to get game time from HA, falling back to SQLite: {e}")
        
        # Fallback to SQLite
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        today = today_date.isoformat()
        
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
        """Get time played for a specific game this week
        
        Uses HA history if available, otherwise falls back to SQLite.
        """
        today = datetime.now().date()
        week_start = today - timedelta(days=today.weekday())
        week_end = week_start + timedelta(days=6)
        
        # Try HA history first (single source of truth)
        if self._is_ha_available():
            try:
                from ha.history import get_game_times_from_ha
                game_times = get_game_times_from_ha(self.ha_client, user, week_start, week_end)
                ha_time = game_times.get(game, 0.0)
                logger.debug(f"User {user} game {game} weekly from HA: {ha_time:.1f} min")
                
                # Add time from active sessions for this game
                active_time = 0
                for session_id, session in self.active_sessions.items():
                    if session['user'] == user and session['game'] == game:
                        session_date = session['start_time'].date()
                        if session_date >= week_start:
                            elapsed = (datetime.now() - session['start_time']).total_seconds()
                            active_time += elapsed / 60
                
                total_time = ha_time + active_time
                return int(round(total_time))
            except Exception as e:
                logger.warning(f"Failed to get game weekly time from HA, falling back to SQLite: {e}")
        
        # Fallback to SQLite
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        week_start_str = week_start.isoformat()
        
        c.execute('''SELECT SUM(minutes_played) FROM game_stats 
                     WHERE user=? AND game=? AND date >= ?''',
                 (user, game, week_start_str))
        
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
        """Get time played for a specific game this month
        
        Uses HA history if available, otherwise falls back to SQLite.
        """
        today = datetime.now().date()
        month_start = today.replace(day=1)
        # Calculate month end
        if today.month == 12:
            month_end = today.replace(year=today.year + 1, month=1, day=1) - timedelta(days=1)
        else:
            month_end = today.replace(month=today.month + 1, day=1) - timedelta(days=1)
        
        # Try HA history first (single source of truth)
        if self._is_ha_available():
            try:
                from ha.history import get_game_times_from_ha
                game_times = get_game_times_from_ha(self.ha_client, user, month_start, month_end)
                ha_time = game_times.get(game, 0.0)
                logger.debug(f"User {user} game {game} monthly from HA: {ha_time:.1f} min")
                
                # Add time from active sessions for this game
                active_time = 0
                for session_id, session in self.active_sessions.items():
                    if session['user'] == user and session['game'] == game:
                        session_date = session['start_time'].date()
                        if session_date >= month_start:
                            elapsed = (datetime.now() - session['start_time']).total_seconds()
                            active_time += elapsed / 60
                
                total_time = ha_time + active_time
                return int(round(total_time))
            except Exception as e:
                logger.warning(f"Failed to get game monthly time from HA, falling back to SQLite: {e}")
        
        # Fallback to SQLite
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        month_start_str = month_start.isoformat()
        
        c.execute('''SELECT SUM(minutes_played) FROM game_stats 
                     WHERE user=? AND game=? AND date >= ?''',
                 (user, game, month_start_str))
        
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
        """Get stats for all games played by user, organized by period
        
        Uses HA history if available, otherwise falls back to SQLite.
        """
        games = set()
        
        # Try to get games from HA history first
        if self._is_ha_available():
            try:
                # Query last 90 days to get all games
                start_date = (datetime.now() - timedelta(days=90)).date()
                end_date = datetime.now().date()
                from ha.history import get_game_times_from_ha
                game_times = get_game_times_from_ha(self.ha_client, user, start_date, end_date)
                games.update(game_times.keys())
            except Exception as e:
                logger.warning(f"Failed to get games from HA, falling back to SQLite: {e}")
        
        # Fallback to SQLite or add from sessions
        if not games or not self._is_ha_available():
            conn = sqlite3.connect(self.db_path)
            c = conn.cursor()
            
            c.execute('''SELECT DISTINCT game FROM game_stats WHERE user=? 
                         UNION 
                         SELECT DISTINCT game FROM sessions WHERE user=?''',
                     (user, user))
            
            games.update([row[0] for row in c.fetchall()])
            conn.close()
        
        # Add games from active sessions
        for session_id, session in self.active_sessions.items():
            if session['user'] == user and session['game']:
                games.add(session['game'])
        
        # Remove None/empty games
        games = {g for g in games if g and g not in ('None', 'Unknown Game', '')}
        
        # Get stats for each game (these methods now use HA history)
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
            return {'daily_limit_minutes': result[0], 'enabled': result[1]}
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
    
    def check_stale_sessions(self, timeout_minutes=5):
        """Check for stale sessions that haven't received MQTT updates and end them
        
        Args:
            timeout_minutes: Number of minutes without MQTT update before considering session stale
        """
        now = datetime.now()
        timeout_threshold = timedelta(minutes=timeout_minutes)
        stale_sessions = []
        
        for session_id, session in self.active_sessions.items():
            last_update = session.get('last_update', session['start_time'])
            time_since_update = now - last_update
            
            if time_since_update > timeout_threshold:
                stale_sessions.append(session_id)
                logger.warning(f"Session {session_id} is stale (no updates for {time_since_update.total_seconds()/60:.1f} min), ending session")
        
        # End stale sessions
        for session_id in stale_sessions:
            self.end_session(session_id)

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
        # Handle both dict and old format
        if isinstance(limit, dict):
            daily_limit = limit.get('daily_limit_minutes')
        else:
            daily_limit = limit
        return time_today >= daily_limit if daily_limit else False
    
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

