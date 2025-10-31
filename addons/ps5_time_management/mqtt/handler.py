"""MQTT message handlers for PS5 Time Management add-on"""
import logging
from datetime import datetime

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
                if publish_user_sensors_func:
                    publish_user_sensors_func(player)
    
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
                        apply_shutdown_policy_func(player, ps5_id, reason='access_disabled')
                        continue
                    # Daily limit exhausted now (including zero)
                    lim = time_manager.get_user_limit(player)
                    if lim is not None:
                        current = time_manager.get_user_time_today(player)
                        if current >= lim:
                            logger.warning(f"Daily limit reached for {player}; applying shutdown policy")
                            apply_shutdown_policy_func(player, ps5_id, reason='limit_reached')
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

