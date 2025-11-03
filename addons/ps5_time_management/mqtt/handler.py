"""MQTT message handlers for PS5 Time Management add-on"""
import logging
from datetime import datetime
from models.time_manager import set_latest_device_status

logger = logging.getLogger(__name__)

# These will be set by main.py via set_dependencies
time_manager = None
mqtt_client = None
mqtt_connected = False
config = {}
discovered_users = set()
latest_device_status = {}
debug_user_name = None
apply_shutdown_policy_func = None
start_shutdown_warning_func = None
update_all_sensor_states_func = None
publish_user_sensors_func = None

# Track previous activity state per PS5 to detect transitions
# Format: {ps5_id: 'playing' | 'idle' | 'none' | None}
previous_activity_state = {}


def set_dependencies(tm, mqtt, mqtt_conn, cfg, discovered, latest_status, debug_user, 
                    shutdown_policy_func, warning_func, sensor_update_func, publish_func):
    """Set dependencies for MQTT handlers"""
    global time_manager, mqtt_client, mqtt_connected, config
    global discovered_users, latest_device_status, debug_user_name
    global apply_shutdown_policy_func, start_shutdown_warning_func, update_all_sensor_states_func
    global publish_user_sensors_func
    time_manager = tm
    mqtt_client = mqtt
    mqtt_connected = mqtt_conn
    config = cfg
    discovered_users = discovered
    latest_device_status = latest_status
    debug_user_name = debug_user
    apply_shutdown_policy_func = shutdown_policy_func
    start_shutdown_warning_func = warning_func
    update_all_sensor_states_func = sensor_update_func
    publish_user_sensors_func = publish_func


def handle_device_update(ps5_id, data):
    """Handle complete device update from ps5-mqtt"""
    logger.debug(f"Processing device update for PS5 {ps5_id}: {data}")
    
    # Extract players from the message
    players = data.get('players', [])
    
    # IMPORTANT: Get previous activity state BEFORE updating latest_device_status
    # so we can detect transitions properly
    power = data.get('power')
    device_status = data.get('device_status')
    activity = data.get('activity')
    
    # Get previous activity state for this PS5 (tracked per device to handle multiple PS5s)
    prev_activity = previous_activity_state.get(ps5_id)
    
    # NOW update latest device status snapshot
    try:
        latest_device_status.update({
            'ps5_id': ps5_id,
            'power': power or latest_device_status.get('power'),
            'device_status': device_status or latest_device_status.get('device_status'),
            'activity': activity or latest_device_status.get('activity'),
            'players': players or [],
            'title_id': data.get('title_id'),
            'title_name': data.get('title_name'),
            'title_image': data.get('title_image'),
            'last_update': datetime.now().isoformat()
        })
        # Update the models module so PS5TimeManager can access it
        set_latest_device_status(latest_device_status)
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
                if publish_user_sensors_func:
                    publish_user_sensors_func(player)
    
    # Handle activity-based sessions - sessions are tied to user activity, not device power
    # Session lifecycle:
    # - activity transitions TO 'playing' = start session
    # - activity transitions FROM 'playing' (to 'idle', 'none', or device goes offline) = end session
    
    # Detect activity transition TO 'playing'
    activity_transitioned_to_playing = (activity == 'playing' and prev_activity != 'playing')
    # Detect activity transition FROM 'playing'
    activity_transitioned_from_playing = (prev_activity == 'playing' and activity != 'playing')
    
    # Update stored activity state for this PS5 (after checking transitions)
    previous_activity_state[ps5_id] = activity
    
    # Handle transition TO 'playing': Start session
    if activity_transitioned_to_playing and players:
        logger.info(f"Activity transitioned to 'playing' on PS5 {ps5_id} - starting session(s)")
        for player in players:
            if player:
                # Check for existing session (shouldn't exist, but defensive)
                existing_session = None
                for session_id, session in time_manager.active_sessions.items():
                    if session['user'] == player and session.get('ps5_id') == ps5_id:
                        existing_session = session_id
                        break
                
                if existing_session:
                    logger.debug(f"Session already exists for {player} on PS5 {ps5_id}, skipping")
                    continue
                
                game_name = data.get('title_name', 'Unknown Game')
                # Check access and limits
                try:
                    if not time_manager.get_user_access(player):
                        logger.warning(f"Access disabled for {player}; applying shutdown policy")
                        apply_shutdown_policy_func(player, ps5_id, reason='access_disabled')
                        continue
                    # Check day-specific limit (returns None if no limit set)
                    lim = time_manager.get_user_limit_for_today(player)
                    if lim is not None:
                        # If limit is 0, they can't play at all - immediate shutdown
                        if lim == 0:
                            logger.warning(f"User {player} has 0 minutes allowed today; enforcing immediate standby")
                            apply_shutdown_policy_func(player, ps5_id, reason='limit_reached', immediate=True)
                            continue
                        
                        current = time_manager.get_user_time_today(player)
                        if current >= lim:
                            logger.warning(f"Daily limit reached for {player}; applying shutdown policy")
                            apply_shutdown_policy_func(player, ps5_id, reason='limit_reached')
                            continue
                except Exception:
                    pass
                # Cache game image
                try:
                    if data.get('title_image') and game_name:
                        time_manager.cache_game_image(game_name, data.get('title_image'))
                except Exception:
                    pass
                # Check access again
                if not time_manager.get_user_access(player):
                    logger.warning(f"Access blocked for {player}; enforcing action")
                    try:
                        start_shutdown_warning_func(player, ps5_id)
                    except Exception as e:
                        logger.error(f"Failed to start warning for {player}: {e}")
                    continue
                
                session_id = time_manager.start_session(player, game_name, ps5_id)
                if session_id:
                    if debug_user_name and debug_user_name == player:
                        logger.info(f"[DEBUG:{player}] Session started (ID: {session_id}) for game {game_name}")
                    else:
                        logger.info(f"Started session for {player} playing {game_name} (ID: {session_id})")
    
    # Handle transition FROM 'playing': End sessions
    elif activity_transitioned_from_playing:
        logger.info(f"Activity transitioned from 'playing' to '{activity}' on PS5 {ps5_id} - ending session(s)")
        for session_id, session in list(time_manager.active_sessions.items()):
            if session['ps5_id'] == ps5_id:
                time_manager.end_session(session_id)
                logger.info(f"Ended session due to activity change from 'playing' to '{activity}'")
    
    # Handle game updates while activity='playing' (game switches within same session)
    elif activity == 'playing' and players:
        # Update game name if it changed for existing sessions
        for player in players:
            if player:
                for session_id, session in time_manager.active_sessions.items():
                    if session['user'] == player and session.get('ps5_id') == ps5_id:
                        current_game = data.get('title_name', 'Unknown Game')
                        if session.get('game') != current_game:
                            session['game'] = current_game
                            logger.debug(f"Updated game for session: {player} now playing {current_game}")
                        break
    
    # Also handle power state transitions as safety net - if device goes to STANDBY or offline, end sessions
    if power == 'STANDBY' or (power == 'UNKNOWN' and device_status == 'offline'):
        # Device went to sleep/offline - end any remaining sessions
        for session_id, session in list(time_manager.active_sessions.items()):
            if session['ps5_id'] == ps5_id:
                time_manager.end_session(session_id)
                logger.info(f"Ended session due to PS5 {ps5_id} going to {power}")
    
    # Update sensor states for all discovered users (only if MQTT is ready)
    if mqtt_connected and mqtt_client is not None:
        update_all_sensor_states_func()


def handle_state_change(ps5_id, data):
    """Handle state change message"""
    logger.debug(f"State change for PS5 {ps5_id}: {data}")
    handle_device_update(ps5_id, data)


def handle_game_change(ps5_id, data):
    """Handle game change message"""
    logger.debug(f"Game change for PS5 {ps5_id}: {data}")
    # These legacy handlers just call handle_device_update
    handle_device_update(ps5_id, data)


def handle_user_change(ps5_id, data):
    """Handle user change message"""
    logger.debug(f"User change for PS5 {ps5_id}: {data}")
    # These legacy handlers just call handle_device_update
    handle_device_update(ps5_id, data)


def handle_activity_change(ps5_id, data):
    """Handle activity change message"""
    logger.debug(f"Activity change for PS5 {ps5_id}: {data}")
    # These legacy handlers just call handle_device_update
    handle_device_update(ps5_id, data)

