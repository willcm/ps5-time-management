"""Web page routes for PS5 Time Management add-on"""
import logging
from flask import render_template, send_from_directory

logger = logging.getLogger(__name__)

# These will be set by main.py via register_routes
app = None
time_manager = None
discovered_users = set()


def register_routes(flask_app, tm, discovered):
    """Register web page routes with Flask app"""
    global app, time_manager, discovered_users
    app = flask_app
    time_manager = tm
    discovered_users = discovered
    
    @app.route('/')
    def index():
        """Serve Tailwind-based index page"""
        logger.debug("Index route accessed")
        return render_template('index.html')

    @app.route('/stats/<user>')
    def user_stats_page(user):
        """Serve the detailed stats page for a user"""
        logger.debug(f"Stats page accessed for user: {user}")
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
                    from datetime import datetime
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

    @app.route('/admin')
    def admin_page():
        """Serve the admin settings page (PIN protected on client side)"""
        logger.debug("Admin page accessed")
        return render_template('admin.html')

