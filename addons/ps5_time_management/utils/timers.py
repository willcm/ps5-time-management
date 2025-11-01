"""Timer checking utilities for PS5 Time Management"""
import time
import logging

logger = logging.getLogger(__name__)


def check_timers(time_manager, config, apply_shutdown_policy_func):
    """Background thread to check timers and enforce limits
    
    Also checks for stale sessions and ends them if they haven't received MQTT updates.
    
    Args:
        time_manager: PS5TimeManager instance
        config: Configuration dictionary
        apply_shutdown_policy_func: Function to apply shutdown policy
    """
    while True:
        try:
            time.sleep(60)  # Check every minute
            
            # First, check for stale sessions (sessions without MQTT updates)
            session_timeout_minutes = config.get('session_timeout_minutes', 5)
            time_manager.check_stale_sessions(timeout_minutes=session_timeout_minutes)
            
            for session_id, session in list(time_manager.active_sessions.items()):
                user = session['user']
                
                # Check if limit exceeded
                if time_manager.check_limit_exceeded(user):
                    # Trigger 60s warning, then shutdown
                    logger.warning(f"User {user} has exceeded their time limit")
                    time_manager.add_notification(user, 'limit_exceeded', 
                        "Your time limit has been reached for today")
                    if config.get('enable_auto_shutdown'):
                        apply_shutdown_policy_func(user, session['ps5_id'], reason='limit_exceeded')
                
                # Check for warning before shutdown
                elif config.get('graceful_shutdown_warnings'):
                    limit_obj = time_manager.get_user_limit(user)
                    # Handle both dict and old format
                    if isinstance(limit_obj, dict):
                        limit = limit_obj.get('daily_limit_minutes')
                    elif limit_obj is not None:
                        limit = limit_obj
                    else:
                        limit = None
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

