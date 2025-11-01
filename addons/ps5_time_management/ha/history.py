"""Query Home Assistant history for time calculations and game tracking"""
import logging
from datetime import datetime, timedelta
from typing import List, Dict, Optional, Tuple

logger = logging.getLogger(__name__)


def calculate_time_from_binary_sensor_history(history: List[Dict], start_time: datetime, end_time: datetime) -> float:
    """Calculate total time a binary sensor was ON from HA history
    
    Args:
        history: List of state change dicts from HA API
        start_time: Start of period to calculate
        end_time: End of period to calculate
    
    Returns:
        Total minutes the sensor was ON
    """
    if not history:
        return 0.0
    
    total_seconds = 0.0
    last_state = None
    last_timestamp = start_time
    
    for state_change in history:
        timestamp_str = state_change.get('last_changed') or state_change.get('last_updated')
        if not timestamp_str:
            continue
        
        try:
            # Parse ISO format timestamp
            if timestamp_str.endswith('+00:00') or timestamp_str.endswith('Z'):
                timestamp = datetime.fromisoformat(timestamp_str.replace('Z', '+00:00'))
            else:
                timestamp = datetime.fromisoformat(timestamp_str)
        except Exception as e:
            logger.debug(f"Failed to parse timestamp {timestamp_str}: {e}")
            continue
        
        state = state_change.get('state', '').upper()
        is_on = state in ('ON', 'TRUE', '1', 'ACTIVE')
        
        # Only count time if previous state was ON
        if last_state == 'ON' and timestamp > last_timestamp:
            # Add time from last change to this change
            duration = (timestamp - last_timestamp).total_seconds()
            if duration > 0:
                total_seconds += duration
        
        last_state = 'ON' if is_on else 'OFF'
        last_timestamp = timestamp
    
    # If ended in ON state, add time from last change to end_time
    if last_state == 'ON' and end_time > last_timestamp:
        duration = (end_time - last_timestamp).total_seconds()
        if duration > 0:
            total_seconds += duration
    
    return total_seconds / 60.0  # Convert to minutes


def calculate_game_time_from_history(history: List[Dict], start_date: datetime.date, end_date: datetime.date) -> Dict[str, float]:
    """Calculate time played per game from HA history state transitions
    
    Args:
        history: List of state change dicts from HA API for game sensor
        start_date: Start date for calculation
        end_date: End date for calculation
    
    Returns:
        Dict mapping game name to minutes played
    """
    if not history:
        return {}
    
    game_times: Dict[str, float] = {}
    last_game = None
    last_timestamp = datetime.combine(start_date, datetime.min.time())
    
    for state_change in history:
        timestamp_str = state_change.get('last_changed') or state_change.get('last_updated')
        if not timestamp_str:
            continue
        
        try:
            if timestamp_str.endswith('+00:00') or timestamp_str.endswith('Z'):
                timestamp = datetime.fromisoformat(timestamp_str.replace('Z', '+00:00'))
            else:
                timestamp = datetime.fromisoformat(timestamp_str)
        except Exception as e:
            logger.debug(f"Failed to parse timestamp {timestamp_str}: {e}")
            continue
        
        # Only process states within our date range
        if timestamp.date() < start_date or timestamp.date() > end_date:
            continue
        
        current_game = state_change.get('state', 'None')
        # Normalize "None" or empty states
        if not current_game or current_game.lower() in ('none', 'unknown', ''):
            current_game = None
        
        # If we had a previous game, accumulate time
        if last_game and last_game != 'None' and timestamp > last_timestamp:
            duration_minutes = (timestamp - last_timestamp).total_seconds() / 60.0
            if duration_minutes > 0:
                game_times[last_game] = game_times.get(last_game, 0.0) + duration_minutes
        
        last_game = current_game
        last_timestamp = timestamp
    
    # If still in a game at end, add time from last change to end of period
    if last_game and last_game != 'None':
        end_datetime = datetime.combine(end_date, datetime.max.time())
        if end_datetime > last_timestamp:
            duration_minutes = (end_datetime - last_timestamp).total_seconds() / 60.0
            if duration_minutes > 0:
                game_times[last_game] = game_times.get(last_game, 0.0) + duration_minutes
    
    return game_times


def get_daily_time_from_ha(client, user: str, date: datetime.date) -> float:
    """Get total time played for a user on a specific date from HA history
    
    Args:
        client: HomeAssistantClient instance
        user: Username
        date: Date to calculate for
    
    Returns:
        Total minutes played on that date
    """
    entity_id = f"binary_sensor.ps5_time_management_{user.lower()}_session_active"
    
    start_time = datetime.combine(date, datetime.min.time())
    end_time = datetime.combine(date, datetime.max.time())
    
    history = client.get_history(entity_id, start_time=start_time, end_time=end_time)
    if not history:
        return 0.0
    
    return calculate_time_from_binary_sensor_history(history, start_time, end_time)


def get_weekly_time_from_ha(client, user: str, week_start: datetime.date) -> float:
    """Get total time played for a user in a week from HA history
    
    Args:
        client: HomeAssistantClient instance
        user: Username
        week_start: Monday of the week
    
    Returns:
        Total minutes played that week
    """
    entity_id = f"binary_sensor.ps5_time_management_{user.lower()}_session_active"
    
    start_time = datetime.combine(week_start, datetime.min.time())
    end_time = start_time + timedelta(days=7)
    
    history = client.get_history(entity_id, start_time=start_time, end_time=end_time)
    if not history:
        return 0.0
    
    return calculate_time_from_binary_sensor_history(history, start_time, end_time)


def get_monthly_time_from_ha(client, user: str, year: int, month: int) -> float:
    """Get total time played for a user in a month from HA history
    
    Args:
        client: HomeAssistantClient instance
        user: Username
        year: Year
        month: Month (1-12)
    
    Returns:
        Total minutes played that month
    """
    entity_id = f"binary_sensor.ps5_time_management_{user.lower()}_session_active"
    
    start_time = datetime(year, month, 1)
    if month == 12:
        end_time = datetime(year + 1, 1, 1)
    else:
        end_time = datetime(year, month + 1, 1)
    
    history = client.get_history(entity_id, start_time=start_time, end_time=end_time)
    if not history:
        return 0.0
    
    return calculate_time_from_binary_sensor_history(history, start_time, end_time)


def get_game_times_from_ha(client, user: str, start_date: datetime.date, end_date: datetime.date) -> Dict[str, float]:
    """Get time played per game for a user from HA history
    
    Args:
        client: HomeAssistantClient instance
        user: Username
        start_date: Start date
        end_date: End date
    
    Returns:
        Dict mapping game name to minutes played
    """
    entity_id = f"sensor.ps5_time_management_{user.lower()}_game"
    
    start_time = datetime.combine(start_date, datetime.min.time())
    end_time = datetime.combine(end_date, datetime.max.time())
    
    history = client.get_history(entity_id, start_time=start_time, end_time=end_time)
    if not history:
        return {}
    
    return calculate_game_time_from_history(history, start_date, end_date)

