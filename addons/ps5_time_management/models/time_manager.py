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
                      ended_normally BOOLEAN DEFAULT 1,
                      active BOOLEAN DEFAULT 0)''')
        
        # Add 'active' column if it doesn't exist (for migration)
        try:
            c.execute("ALTER TABLE sessions ADD COLUMN active BOOLEAN DEFAULT 0")
        except sqlite3.OperationalError:
            pass  # Column already exists
        
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
                      enabled BOOLEAN DEFAULT 1,
                      monday_limit INTEGER,
                      tuesday_limit INTEGER,
                      wednesday_limit INTEGER,
                      thursday_limit INTEGER,
                      friday_limit INTEGER,
                      saturday_limit INTEGER,
                      sunday_limit INTEGER)''')
        
        # Add per-day limit columns if they don't exist (migration)
        day_columns = ['monday_limit', 'tuesday_limit', 'wednesday_limit', 
                       'thursday_limit', 'friday_limit', 'saturday_limit', 'sunday_limit']
        for day_col in day_columns:
            try:
                c.execute(f"ALTER TABLE user_limits ADD COLUMN {day_col} INTEGER")
            except sqlite3.OperationalError:
                pass  # Column already exists
        
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

        # Global settings table - admin-configurable settings
        c.execute('''CREATE TABLE IF NOT EXISTS global_settings
                     (key TEXT PRIMARY KEY,
                      value TEXT)''')

        conn.commit()
        conn.close()
        logger.info("Database initialized successfully")
    
    def start_session(self, user, game, ps5_id):
        """Start a new gaming session"""
        # Safety check: Prevent duplicate sessions for same user on same PS5
        # (Handler should prevent this, but this is a defensive check)
        for session_id, s in self.active_sessions.items():
            if s['user'] == user and s.get('ps5_id') == ps5_id:
                logger.debug(f"Duplicate session suppressed for {user} on PS5 {ps5_id} (existing session: {session_id})")
                return False
        
        session_id = f"{ps5_id}:{user}:{int(time.time())}"
        start_time = datetime.now()
        self.active_sessions[session_id] = {
            'user': user,
            'game': game,
            'start_time': start_time,
            'ps5_id': ps5_id,
            'warnings_sent': []
        }
        
        # Persist active session to database immediately
        try:
            conn = sqlite3.connect(self.db_path)
            c = conn.cursor()
            # Store session with active=1 and no end_time
            c.execute('''INSERT INTO sessions 
                         (user, game, start_time, ps5_id, active, ended_normally)
                         VALUES (?, ?, ?, ?, 1, 0)''',
                     (user, game, start_time, ps5_id))
            # Get the database ID for this session
            db_id = c.lastrowid
            conn.commit()
            conn.close()
            # Store DB ID in session dict for later reference
            self.active_sessions[session_id]['db_id'] = db_id
            logger.info(f"Started session for user {user} playing {game} on PS5 {ps5_id}")
        except Exception as e:
            logger.warning(f"Failed to persist session to database: {e}")
            # Still return session_id even if DB write failed
        
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
        db_id = session.get('db_id')
        
        # Update existing session in database (if it was persisted)
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        
        if db_id:
            # Update the existing session record
            c.execute('''UPDATE sessions 
                         SET end_time=?, duration_seconds=?, active=0, ended_normally=1
                         WHERE id=?''',
                     (end_time, int(duration), db_id))
        else:
            # Fallback: insert new record if no DB ID found
            c.execute('''INSERT INTO sessions 
                         (user, game, start_time, end_time, duration_seconds, ps5_id, active)
                         VALUES (?, ?, ?, ?, ?, ?, 0)''',
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
    
    def get_active_sessions_from_db(self):
        """Get all active sessions from database (sessions with active=1 or end_time IS NULL)"""
        try:
            conn = sqlite3.connect(self.db_path)
            c = conn.cursor()
            c.execute('''SELECT id, user, game, start_time, ps5_id 
                         FROM sessions 
                         WHERE (active = 1 OR end_time IS NULL)''')
            rows = c.fetchall()
            conn.close()
            # Convert to list of dicts
            sessions = []
            for row in rows:
                start_time = datetime.fromisoformat(row[3]) if isinstance(row[3], str) else row[3]
                session_info = {
                    'db_id': row[0],
                    'user': row[1],
                    'game': row[2],
                    'start_time': start_time,
                    'ps5_id': row[4]
                }
                sessions.append(session_info)
            return sessions
        except Exception as e:
            logger.warning(f"Failed to load active sessions from database: {e}")
            return []
    
    def restore_session(self, db_id, user, game, start_time, ps5_id):
        """Restore a session to active_sessions dict from database"""
        session_id = f"{ps5_id}:{user}:{int(start_time.timestamp())}"
        self.active_sessions[session_id] = {
            'user': user,
            'game': game,
            'start_time': start_time,
            'ps5_id': ps5_id,
            'warnings_sent': [],
            'db_id': db_id  # Keep reference to DB ID
        }
        logger.info(f"Restored session for {user} playing {game} on PS5 {ps5_id}")
        return session_id
    
    def mark_session_ended(self, db_id, end_time=None, ended_normally=True):
        """Mark a session as ended in the database without restoring it to active_sessions"""
        if end_time is None:
            end_time = datetime.now()
        try:
            conn = sqlite3.connect(self.db_path)
            c = conn.cursor()
            # Get start_time to calculate duration
            c.execute('SELECT user, game, start_time, ps5_id FROM sessions WHERE id=?', (db_id,))
            row = c.fetchone()
            if row:
                user = row[0]
                game = row[1]
                start_time = datetime.fromisoformat(row[2]) if isinstance(row[2], str) else row[2]
                ps5_id = row[3]
                duration = (end_time - start_time).total_seconds()
                c.execute('''UPDATE sessions 
                             SET end_time=?, duration_seconds=?, active=0, ended_normally=?
                             WHERE id=?''',
                         (end_time, int(duration), 1 if ended_normally else 0, db_id))
                conn.commit()
                logger.info(f"Marked session {db_id} as ended for {user} ({int(duration/60)} minutes)")
            conn.close()
        except Exception as e:
            logger.warning(f"Failed to mark session {db_id} as ended: {e}")
    
    def log_all_active_sessions(self):
        """Log summary of all currently active sessions"""
        if not self.active_sessions:
            logger.info("No active sessions currently")
            return
        
        logger.info(f"=== ACTIVE SESSIONS SUMMARY: {len(self.active_sessions)} session(s) ===")
        now = datetime.now()
        for session_id, session in self.active_sessions.items():
            elapsed = (now - session['start_time']).total_seconds()
            elapsed_minutes = elapsed / 60
            logger.info(f"  Session ID: {session_id} | User: {session['user']} | Game: {session['game']} | "
                       f"PS5: {session.get('ps5_id', 'N/A')} | DB ID: {session.get('db_id', 'N/A')} | "
                       f"Started: {session['start_time']} | Elapsed: {elapsed_minutes:.1f} minutes")
        logger.info("=== END ACTIVE SESSIONS SUMMARY ===")
    
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
        logger.debug(f"User {user} time today: {completed_time} min completed (from DB) + {active_time:.1f} min active ({active_count} sessions) = {total_time:.1f} min total")
        return int(round(total_time))  # Round instead of truncate for better accuracy
    
    def get_user_weekly_time(self, user):
        """Get total time played in last 7 days by user (including active sessions)"""
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        
        # Calculate last 7 days start date
        today = datetime.now().date()
        seven_days_ago = today - timedelta(days=7)
        seven_days_ago_str = seven_days_ago.isoformat()
        
        # Get completed sessions from database for last 7 days
        c.execute('''SELECT SUM(total_minutes) FROM user_stats 
                     WHERE user=? AND date >= ?''',
                 (user, seven_days_ago_str))
        
        result = c.fetchone()
        completed_time = result[0] if result and result[0] is not None else 0
        conn.close()
        
        # Add time from active sessions (if they started in last 7 days)
        active_time = 0
        for session_id, session in self.active_sessions.items():
            if session['user'] == user:
                session_date = session['start_time'].date()
                if session_date >= seven_days_ago:  # Only count sessions from last 7 days
                    elapsed = (datetime.now() - session['start_time']).total_seconds()
                    active_time += elapsed / 60  # Convert to minutes
        
        total_time = completed_time + active_time
        logger.debug(f"User {user} weekly time (last 7 days): {completed_time} min completed (from DB) + {active_time:.1f} min active = {total_time:.1f} min total")
        return int(round(total_time))
    
    def get_user_monthly_time(self, user):
        """Get total time played in last 30 days by user (including active sessions)"""
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        
        # Calculate last 30 days start date
        today = datetime.now().date()
        thirty_days_ago = today - timedelta(days=30)
        thirty_days_ago_str = thirty_days_ago.isoformat()
        
        # Get completed sessions from database for last 30 days
        c.execute('''SELECT SUM(total_minutes) FROM user_stats 
                     WHERE user=? AND date >= ?''',
                 (user, thirty_days_ago_str))
        
        result = c.fetchone()
        completed_time = result[0] if result and result[0] is not None else 0
        conn.close()
        
        # Add time from active sessions (if they started in last 30 days)
        active_time = 0
        for session_id, session in self.active_sessions.items():
            if session['user'] == user:
                session_date = session['start_time'].date()
                if session_date >= thirty_days_ago:  # Only count sessions from last 30 days
                    elapsed = (datetime.now() - session['start_time']).total_seconds()
                    active_time += elapsed / 60  # Convert to minutes
        
        total_time = completed_time + active_time
        logger.debug(f"User {user} monthly time (last 30 days): {completed_time} min completed (from DB) + {active_time:.1f} min active = {total_time:.1f} min total")
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
        """Get time played for a specific game in last 7 days (including active sessions)"""
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        
        # Calculate last 7 days start date
        today = datetime.now().date()
        seven_days_ago = today - timedelta(days=7)
        seven_days_ago_str = seven_days_ago.isoformat()
        
        # Get completed sessions from database for last 7 days
        c.execute('''SELECT SUM(minutes_played) FROM game_stats 
                     WHERE user=? AND game=? AND date >= ?''',
                 (user, game, seven_days_ago_str))
        
        result = c.fetchone()
        completed_time = result[0] if result and result[0] is not None else 0
        conn.close()
        
        # Add time from active sessions for this game (if started in last 7 days)
        active_time = 0
        for session_id, session in self.active_sessions.items():
            if session['user'] == user and session['game'] == game:
                session_date = session['start_time'].date()
                if session_date >= seven_days_ago:
                    elapsed = (datetime.now() - session['start_time']).total_seconds()
                    active_time += elapsed / 60
        
        total_time = completed_time + active_time
        return int(round(total_time))
    
    def get_game_time_monthly(self, user, game):
        """Get time played for a specific game in last 30 days (including active sessions)"""
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        
        # Calculate last 30 days start date
        today = datetime.now().date()
        thirty_days_ago = today - timedelta(days=30)
        thirty_days_ago_str = thirty_days_ago.isoformat()
        
        # Get completed sessions from database for last 30 days
        c.execute('''SELECT SUM(minutes_played) FROM game_stats 
                     WHERE user=? AND game=? AND date >= ?''',
                 (user, game, thirty_days_ago_str))
        
        result = c.fetchone()
        completed_time = result[0] if result and result[0] is not None else 0
        conn.close()
        
        # Add time from active sessions for this game (if started in last 30 days)
        active_time = 0
        for session_id, session in self.active_sessions.items():
            if session['user'] == user and session['game'] == game:
                session_date = session['start_time'].date()
                if session_date >= thirty_days_ago:
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
    
    def get_user_weekly_limits(self, user):
        """Get per-day limits for a user (returns dict with day names and limits)"""
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        
        c.execute('''SELECT monday_limit, tuesday_limit, wednesday_limit, thursday_limit,
                            friday_limit, saturday_limit, sunday_limit
                     FROM user_limits 
                     WHERE user=?''',
                 (user,))
        
        result = c.fetchone()
        conn.close()
        
        if result:
            return {
                'monday': result[0],
                'tuesday': result[1],
                'wednesday': result[2],
                'thursday': result[3],
                'friday': result[4],
                'saturday': result[5],
                'sunday': result[6]
            }
        return None
    
    def set_user_weekly_limits(self, user, limits_dict):
        """Set per-day limits for a user (limits_dict: {'monday': 120, 'tuesday': 60, ...})"""
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        
        # First check if user exists, if not create a row
        c.execute('SELECT user FROM user_limits WHERE user=?', (user,))
        if not c.fetchone():
            c.execute('''INSERT INTO user_limits (user, enabled) VALUES (?, 1)''', (user,))
        
        # Update the per-day limits
        c.execute('''UPDATE user_limits 
                     SET monday_limit=?, tuesday_limit=?, wednesday_limit=?,
                         thursday_limit=?, friday_limit=?, saturday_limit=?,
                         sunday_limit=?
                     WHERE user=?''',
                 (limits_dict.get('monday'), limits_dict.get('tuesday'),
                  limits_dict.get('wednesday'), limits_dict.get('thursday'),
                  limits_dict.get('friday'), limits_dict.get('saturday'),
                  limits_dict.get('sunday'), user))
        
        conn.commit()
        conn.close()
        
        logger.info(f"Set weekly limits for user {user}: {limits_dict}")
    
    def get_user_limit_for_today(self, user):
        """Get the time limit for the user based on today's day of week"""
        from datetime import datetime
        today = datetime.now()
        day_name = today.strftime('%A').lower()  # 'monday', 'tuesday', etc.
        
        weekly_limits = self.get_user_weekly_limits(user)
        if weekly_limits and weekly_limits.get(day_name) is not None:
            return weekly_limits.get(day_name)
        
        # Fallback to daily_limit_minutes if no per-day limit set
        limit_obj = self.get_user_limit(user)
        if limit_obj:
            if isinstance(limit_obj, dict):
                return limit_obj.get('daily_limit_minutes')
            return limit_obj
        return None

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
        """Check if user has exceeded their time limit (uses day-specific limit if set)"""
        daily_limit = self.get_user_limit_for_today(user)
        if daily_limit is None:
            return False
        
        # If limit is 0, they can't play at all
        if daily_limit == 0:
            return True
        
        time_today = self.get_user_time_today(user)
        return time_today >= daily_limit
    
    def get_global_setting(self, key, default=None):
        """Get a global setting value from database"""
        try:
            conn = sqlite3.connect(self.db_path)
            c = conn.cursor()
            c.execute('SELECT value FROM global_settings WHERE key=?', (key,))
            row = c.fetchone()
            conn.close()
            if row:
                return row[0]
            return default
        except Exception as e:
            logger.warning(f"Failed to get global setting '{key}': {e}")
            return default
    
    def set_global_setting(self, key, value):
        """Set a global setting value in database"""
        try:
            conn = sqlite3.connect(self.db_path)
            c = conn.cursor()
            c.execute('''INSERT INTO global_settings (key, value)
                         VALUES (?, ?)
                         ON CONFLICT(key) DO UPDATE SET value=excluded.value''',
                     (key, str(value)))
            conn.commit()
            conn.close()
            logger.info(f"Set global setting '{key}' to '{value}'")
            return True
        except Exception as e:
            logger.error(f"Failed to set global setting '{key}': {e}")
            return False
    
    def get_all_global_settings(self):
        """Get all global settings as a dictionary"""
        try:
            conn = sqlite3.connect(self.db_path)
            c = conn.cursor()
            c.execute('SELECT key, value FROM global_settings')
            rows = c.fetchall()
            conn.close()
            return {row[0]: row[1] for row in rows}
        except Exception as e:
            logger.warning(f"Failed to get all global settings: {e}")
            return {}

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

