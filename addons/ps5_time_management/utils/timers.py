"""Timer checking utilities for PS5 Time Management"""
import time
import logging

logger = logging.getLogger(__name__)


def check_timers(time_manager, config, apply_shutdown_policy_func):
    """Background thread to check timers and enforce limits
    
    Args:
        time_manager: PS5TimeManager instance
        config: Configuration dictionary
        apply_shutdown_policy_func: Function to apply shutdown policy
    """
    while True:
        try:
            time.sleep(60)  # Check every minute
            
            for session_id, session in list(time_manager.active_sessions.items()):
                user = session['user']
                
                # Check if limit exceeded
                limit = time_manager.get_user_limit_for_today(user)
                # Get enable_auto_shutdown from database, fallback to config
                enable_auto_shutdown_db = time_manager.get_global_setting('enable_auto_shutdown')
                if enable_auto_shutdown_db is not None:
                    enable_auto_shutdown = enable_auto_shutdown_db.lower() in ('true', '1', 'yes')
                else:
                    enable_auto_shutdown = config.get('enable_auto_shutdown', True)
                
                if limit is not None and limit == 0:
                    # 0-minute days should be handled at session start, but check here as well
                    logger.warning(f"User {user} has 0 minutes allowed today - enforcing immediate standby")
                    if enable_auto_shutdown:
                        apply_shutdown_policy_func(user, session['ps5_id'], reason='limit_reached', immediate=True)
                elif time_manager.check_limit_exceeded(user):
                    # Trigger shutdown policy (will use warning from config)
                    logger.warning(f"User {user} has exceeded their time limit")
                    time_manager.add_notification(user, 'limit_exceeded', 
                        "Your time limit has been reached for today")
                    if enable_auto_shutdown:
                        apply_shutdown_policy_func(user, session['ps5_id'], reason='limit_exceeded')
                
                # Check for warning before shutdown (defaults to True if not set)
                elif config.get('graceful_shutdown_warnings', True):
                    if limit is not None and limit > 0:  # Only warn if they have a limit set and it's > 0
                        time_today = time_manager.get_user_time_today(user)
                        # Get warning from database, fallback to config
                        warning_from_db = time_manager.get_global_setting('warning_before_shutdown_minutes')
                        if warning_from_db is not None:
                            warning_minutes = int(warning_from_db)
                        else:
                            warning_minutes = config.get('warning_before_shutdown_minutes', 1)
                        remaining = limit - time_today
                        
                        # Warn if we're within the warning window OR if remaining is less than warning (but > 0)
                        if time_today >= (limit - warning_minutes) or (remaining > 0 and remaining < warning_minutes):
                            # If remaining is less than warning time, use the actual remaining time
                            if remaining < warning_minutes and remaining > 0:
                                # Give them the warning with the actual remaining time, but still trigger shutdown policy
                                if 'warning_sent' not in session.get('warnings_sent', []):
                                    session.setdefault('warnings_sent', []).append('warning_sent')
                                    logger.info(f"Sending warning to {user} - only {remaining:.0f} minutes remaining")
                                    time_manager.add_notification(user, 'warning', 
                                        f"You have {remaining:.0f} minutes remaining")
                                    # Since remaining < warning_minutes, trigger shutdown policy immediately
                                    if enable_auto_shutdown:
                                        apply_shutdown_policy_func(user, session['ps5_id'], reason='limit_exceeded')
                            elif 'warning_sent' not in session.get('warnings_sent', []):
                                session.setdefault('warnings_sent', []).append('warning_sent')
                                logger.info(f"Sending warning to {user} - {remaining:.0f} minutes remaining")
                                time_manager.add_notification(user, 'warning', 
                                    f"You have {warning_minutes} minutes remaining")
                            
        except Exception as e:
            logger.error(f"Error in timer check: {e}")

