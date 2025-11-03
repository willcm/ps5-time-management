"""Data cleanup utilities for PS5 Time Management"""
import sqlite3
import logging

logger = logging.getLogger(__name__)


def clear_all_user_data(time_manager, discovered_users, update_all_sensor_states_func):
    """Clear all historic data for all users
    
    Args:
        time_manager: PS5TimeManager instance
        discovered_users: Set of discovered usernames
        update_all_sensor_states_func: Function to update all sensor states
    """
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
        update_all_sensor_states_func()
        
        logger.info(f"Cleared all historic data for {len(cleared_users)} users: {cleared_users}")
        return cleared_users
        
    except Exception as e:
        logger.error(f"Error clearing all user data: {e}")
        return []

