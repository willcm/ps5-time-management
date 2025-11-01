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
    # IMPORTANT: Always update power state if present in the message, even if it's the only field
    try:
        # Get current power state before update
        current_power = latest_device_status.get('power')
        new_power = data.get('power')
        
        # Always update power state if present, even if other fields are missing
        if new_power:
            logger.debug(f"Updating power state for PS5 {ps5_id}: {current_power} -> {new_power}")
        
        new_device_status = data.get('device_status')
        
        # According to ps5-mqtt docs: when offline, power should be "UNKNOWN" (not "STANDBY")
        # device_status: "offline" means unreachable/powered off
        # power: "UNKNOWN" means we don't know the state
        # power: "STANDBY" means device is in rest mode and reachable
        if new_device_status == 'offline':
            # Preserve "UNKNOWN" if ps5-mqtt publishes it, or set it if power field is missing
            if not new_power or new_power == 'STANDBY':
                new_power = 'UNKNOWN'
                logger.debug(f"Device {ps5_id} is offline - power should be UNKNOWN (not STANDBY)")
        
        latest_device_status.update({
            'ps5_id': ps5_id,
            'power': new_power if new_power else latest_device_status.get('power'),
            'device_status': new_device_status if new_device_status else latest_device_status.get('device_status'),
            'activity': data.get('activity', latest_device_status.get('activity')),
            'players': players or [],
            'title_id': data.get('title_id', latest_device_status.get('title_id')),
            'title_name': data.get('title_name', latest_device_status.get('title_name')),
            'title_image': data.get('title_image', latest_device_status.get('title_image')),
            'last_update': datetime.now().isoformat()
        })
        
        # Ensure consistency: if offline, power should be UNKNOWN
        if latest_device_status.get('device_status') == 'offline' and latest_device_status.get('power') not in ('UNKNOWN', None):
            logger.debug(f"Device {ps5_id} status is offline - correcting power to UNKNOWN (was {latest_device_status.get('power')})")
            latest_device_status['power'] = 'UNKNOWN'
        # Update the models module so PS5TimeManager can access it
        set_latest_device_status(latest_device_status)
        
        # Log power state change with WARNING level for visibility (will be colored orange in logs)
        if new_power and new_power != current_power:
            logger.warning(f"🔌 Power state changed for PS5 {ps5_id}: {current_power or 'None'} -> {new_power}")
            # Also log to INFO for compatibility, but WARNING is the highlighted one
            logger.info(f"Power state changed for PS5 {ps5_id}: {current_power or 'None'} -> {new_power}")
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
                    lim_obj = time_manager.get_user_limit(player)
                    if lim_obj is not None:
                        # Handle both dict and old format
                        if isinstance(lim_obj, dict):
                            lim = lim_obj.get('daily_limit_minutes')
                        else:
                            lim = lim_obj
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

                # Check for restored sessions and replace them with proper sessions
                for sid, session in list(time_manager.active_sessions.items()):
                    if session['user'] == player and session.get('restored'):
                        # Replace restored session with proper one
                        del time_manager.active_sessions[sid]
                        logger.info(f"Replaced restored session for {player} with proper session from device update")
                        break
                
                session_id = time_manager.start_session(player, game_name, ps5_id)
                if session_id:
                    if debug_user_name and debug_user_name == player:
                        logger.info(f"[DEBUG:{player}] Session started (ID: {session_id}) for game {game_name}")
                    else:
                        logger.info(f"Started session for {player} playing {game_name} (ID: {session_id})")
                
                # Always update heartbeat for active players (whether new or existing session)
                time_manager.update_session_heartbeat(player, ps5_id)
    elif activity in ['idle', 'none']:
        # Only end sessions for users who are no longer in the players list
        # If players list is empty or doesn't contain the user, end their session
        current_players = set(players or [])
        for session_id, session in list(time_manager.active_sessions.items()):
            if session['ps5_id'] == ps5_id:
                session_user = session['user']
                # End session if user is no longer playing
                if session_user not in current_players:
                    time_manager.end_session(session_id)
                    logger.info(f"Ended session for {session_user} on PS5 {ps5_id} (user no longer in players list)")
                else:
                    # User still playing - update heartbeat
                    time_manager.update_session_heartbeat(session_user, ps5_id)
    
    # Handle power state and device_status - check this early and always process it
    # According to ps5-mqtt documentation:
    # - power: "STANDBY" = device in rest mode, reachable, can be woken
    # - power: "UNKNOWN" + device_status: "offline" = device unreachable/powered off
    # Both cases should end sessions, but are different states
    power = data.get('power')
    device_status = data.get('device_status')
    
    # Use the power value from latest_device_status (which we just updated) to get correct value
    # This ensures we use "UNKNOWN" for offline, not "STANDBY"
    actual_power = latest_device_status.get('power', power)
    actual_device_status = latest_device_status.get('device_status', device_status)
    
    # End sessions when device goes to STANDBY or becomes offline (UNKNOWN)
    should_end_sessions = False
    reason = None
    
    if actual_power == 'STANDBY':
        # Device is in rest mode (reachable)
        should_end_sessions = True
        reason = "standby"
    elif actual_device_status == 'offline' or actual_power == 'UNKNOWN':
        # Device is unreachable/powered off (not in rest mode)
        should_end_sessions = True
        reason = "offline"
        logger.info(f"PS5 {ps5_id} is offline/unreachable (power: {actual_power}) - ending sessions")
    
    if should_end_sessions:
        # End all sessions for this PS5 when it goes to standby or offline
        sessions_ended = []
        for session_id, session in list(time_manager.active_sessions.items()):
            if session['ps5_id'] == ps5_id or session.get('ps5_id') == 'unknown':
                # End session - either matches this PS5 or is a restored session with unknown PS5
                time_manager.end_session(session_id)
                sessions_ended.append(session_id)
                logger.info(f"Ended session {session_id} due to PS5 {ps5_id} going to {reason}")
        
        if sessions_ended:
            logger.info(f"Ended {len(sessions_ended)} session(s) due to PS5 {ps5_id} going to {reason}")
        
        # Clear all restored sessions when device is STANDBY or offline
        restored_sessions = [sid for sid, s in time_manager.active_sessions.items() if s.get('restored')]
        for sid in restored_sessions:
            time_manager.end_session(sid)
            logger.info(f"Cleared restored session {sid} because device is {reason}")
    
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

