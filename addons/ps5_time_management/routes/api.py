"""API routes for PS5 Time Management add-on"""
import os
import json
import sqlite3
import logging
from flask import jsonify, request, render_template
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

# These will be set by main.py via register_routes
app = None
time_manager = None
discovered_users = set()
mqtt_connected = False
mqtt_client = None
publish_user_sensors_func = None
update_user_sensor_states_func = None
latest_device_status = {}
debug_user_name = None


def register_routes(flask_app, tm, discovered, mqtt_conn, mqtt_cli, publish_func, update_func, 
                   latest_status, debug_user, cfg=None):
    """Register API routes with Flask app"""
    global app, time_manager, discovered_users, mqtt_connected, mqtt_client
    global publish_user_sensors_func, update_user_sensor_states_func, latest_device_status, debug_user_name
    global config
    app = flask_app
    time_manager = tm
    discovered_users = discovered
    mqtt_connected = mqtt_conn
    mqtt_client = mqtt_cli
    publish_user_sensors_func = publish_func
    update_user_sensor_states_func = update_func
    latest_device_status = latest_status
    debug_user_name = debug_user
    config = cfg or {}
    
    # Register all routes
    register_health_routes()
    register_status_routes()
    register_user_routes()
    register_stats_routes()
    register_game_routes()
    register_limit_routes()
    register_session_routes()
    register_debug_routes()
    register_mqtt_routes()
    register_admin_routes()


def register_health_routes():
    """Register health check routes"""
    @app.route('/api/health', methods=['GET'])
    def health_check():
        """Health check endpoint"""
        return jsonify({'status': 'ok'})


def register_status_routes():
    """Register status routes"""
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


def register_user_routes():
    """Register user-related routes"""
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
        
        # Get breakdown for debugging
        daily = time_manager.get_user_time_today(user)
        weekly = time_manager.get_user_weekly_time(user)
        monthly = time_manager.get_user_monthly_time(user)
        
        # Calculate remaining time using day-specific limit
        daily_limit = time_manager.get_user_limit_for_today(user)
        if daily_limit is None:
            # Fallback to database global setting, then config default if no user limit set
            default_from_db = time_manager.get_global_setting('default_daily_limit_minutes')
            if default_from_db is not None:
                daily_limit = int(default_from_db)
            else:
                daily_limit = config.get('default_daily_limit_minutes', 120)
        remaining = max(0, daily_limit - daily) if daily_limit is not None else None
        
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
            'remaining': remaining,
            'active_sessions': active_session_info,
            'top_games': time_manager.get_top_games(user, 30, 10),
            'games': time_manager.get_all_games_stats(user)  # Per-game breakdown by period
        })


def register_stats_routes():
    """Register stats routes"""
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


def register_game_routes():
    """Register game-related routes"""
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


def register_limit_routes():
    """Register time limit routes"""
    @app.route('/api/limits/<user>', methods=['GET'])
    def get_limit(user):
        """Get time limit for user (uses day-specific limit if set)"""
        daily_limit = time_manager.get_user_limit_for_today(user)
        current_time = time_manager.get_user_time_today(user)
        
        return jsonify({
            'daily_limit': daily_limit,
            'current_time': current_time,
            'remaining': (daily_limit - current_time) if daily_limit is not None else None
        })

    @app.route('/api/limits/<user>', methods=['POST'])
    def set_limit(user):
        """Set time limit for user"""
        data = request.json
        daily_minutes = data.get('daily_minutes')
        enabled = data.get('enabled', True)
        
        time_manager.set_user_limit(user, daily_minutes, enabled)
        return jsonify({'status': 'success'})


def register_session_routes():
    """Register session-related routes"""
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


def register_debug_routes():
    """Register debug routes"""
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

    @app.route('/api/debug_user', methods=['GET', 'POST'])
    def api_debug_user():
        """Get or set the per-user debug filter."""
        global debug_user_name
        if request.method == 'GET':
            return jsonify({'debug_user': debug_user_name})
        try:
            data = request.get_json(force=True) or {}
            # Update the global in this module (will be synced to main.py)
            debug_user_name = data.get('debug_user') or None
            logger.info(f"Per-user debug set to: {debug_user_name}")
            return jsonify({'debug_user': debug_user_name})
        except Exception as e:
            return jsonify({'error': str(e)}), 400

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
        update_user_sensor_states_func(user)
        
        logger.info(f"Cleaned up all data for user {user} and updated sensor states")
        
        return jsonify({
            'message': f'Cleaned up all data for user {user} and updated sensor states',
            'user': user
        })

    @app.route('/api/refresh/<user>', methods=['POST'])
    def refresh_user_sensors(user):
        """Manually refresh sensor states for a user"""
        try:
            update_user_sensor_states_func(user)
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


def register_mqtt_routes():
    """Register MQTT-related routes"""
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

    @app.route('/api/republish_discovery', methods=['POST'])
    def api_republish_all_discovery():
        """Republish MQTT discovery for all known users (useful after updates)."""
        if not mqtt_connected or mqtt_client is None:
            return jsonify({'error': 'MQTT not connected'}), 503
        count = 0
        for user in list(discovered_users):
            try:
                publish_user_sensors_func(user)
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
            publish_user_sensors_func(user)
            return jsonify({'republished': 1, 'user': user})
        except Exception as e:
            return jsonify({'error': str(e)}), 500

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


def register_admin_routes():
    """Register admin routes for PIN-protected admin area"""
    
    @app.route('/api/admin/verify-pin', methods=['POST'])
    def verify_pin():
        """Verify admin PIN"""
        data = request.json
        submitted_pin = data.get('pin', '')
        admin_pin = config.get('admin_pin', '0000')
        
        if submitted_pin == admin_pin:
            return jsonify({'success': True, 'message': 'PIN verified'})
        else:
            return jsonify({'success': False, 'message': 'Invalid PIN'}), 401
    
    @app.route('/api/admin/users', methods=['GET'])
    def get_admin_users():
        """Get list of discovered users for admin management"""
        # Return discovered users sorted alphabetically
        users_list = sorted(list(discovered_users))
        return jsonify({'users': users_list})
    
    @app.route('/api/admin/limits/<user>', methods=['GET'])
    def get_admin_limits(user):
        """Get weekly limits for a user"""
        if user not in discovered_users:
            return jsonify({'error': 'User not found'}), 404
        
        weekly_limits = time_manager.get_user_weekly_limits(user)
        if weekly_limits is None:
            # Return empty limits dict
            weekly_limits = {
                'monday': None,
                'tuesday': None,
                'wednesday': None,
                'thursday': None,
                'friday': None,
                'saturday': None,
                'sunday': None
            }
        
        return jsonify({
            'user': user,
            'limits': weekly_limits
        })
    
    @app.route('/api/admin/limits/<user>', methods=['POST'])
    def set_admin_limits(user):
        """Set weekly limits for a user"""
        if user not in discovered_users:
            return jsonify({'error': 'User not found'}), 404
        
        data = request.json
        limits = data.get('limits', {})
        
        # Validate limits are integers or None
        day_names = ['monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday']
        validated_limits = {}
        for day in day_names:
            value = limits.get(day)
            if value is not None:
                try:
                    validated_limits[day] = int(value)
                    if validated_limits[day] < 0:
                        return jsonify({'error': f'{day} limit must be >= 0'}), 400
                except (ValueError, TypeError):
                    return jsonify({'error': f'{day} limit must be a number'}), 400
            else:
                validated_limits[day] = None
        
        time_manager.set_user_weekly_limits(user, validated_limits)
        return jsonify({'success': True, 'message': f'Weekly limits updated for {user}'})
    
    @app.route('/api/admin/settings', methods=['GET'])
    def get_global_settings():
        """Get global settings (with fallback to config)"""
        # Get from database, fallback to config defaults
        default_daily_limit = time_manager.get_global_setting('default_daily_limit_minutes')
        warning_minutes = time_manager.get_global_setting('warning_before_shutdown_minutes')
        enable_auto_shutdown = time_manager.get_global_setting('enable_auto_shutdown')
        
        # Fallback to config if not in database
        if default_daily_limit is None:
            default_daily_limit = config.get('default_daily_limit_minutes', 120)
        else:
            default_daily_limit = int(default_daily_limit)
        
        if warning_minutes is None:
            warning_minutes = config.get('warning_before_shutdown_minutes', 1)
        else:
            warning_minutes = int(warning_minutes)
        
        if enable_auto_shutdown is None:
            enable_auto_shutdown = config.get('enable_auto_shutdown', True)
        else:
            enable_auto_shutdown = enable_auto_shutdown.lower() in ('true', '1', 'yes')
        
        return jsonify({
            'default_daily_limit_minutes': default_daily_limit,
            'warning_before_shutdown_minutes': warning_minutes,
            'enable_auto_shutdown': enable_auto_shutdown
        })
    
    @app.route('/api/admin/settings', methods=['POST'])
    def set_global_settings():
        """Set global settings"""
        data = request.json
        
        if 'default_daily_limit_minutes' in data:
            time_manager.set_global_setting('default_daily_limit_minutes', int(data['default_daily_limit_minutes']))
        
        if 'warning_before_shutdown_minutes' in data:
            time_manager.set_global_setting('warning_before_shutdown_minutes', int(data['warning_before_shutdown_minutes']))
        
        if 'enable_auto_shutdown' in data:
            time_manager.set_global_setting('enable_auto_shutdown', bool(data['enable_auto_shutdown']))
        
        return jsonify({'status': 'success'})

