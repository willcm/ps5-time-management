#!/usr/bin/env bashio

bashio::log.info "Starting PS5 Time Management add-on..."

# Debug: Test if bashio::services works
bashio::log.info "Testing bashio::services 'mqtt' call..."
MQTT_SERVICE_RESULT=$(bashio::services 'mqtt' 2>&1)
bashio::log.info "bashio::services 'mqtt' result: '${MQTT_SERVICE_RESULT}'"

# Debug: Test individual service calls
bashio::log.info "Testing individual MQTT service calls..."
bashio::log.info "MQTT Host: '$(bashio::services 'mqtt' 'host' 2>&1)'"
bashio::log.info "MQTT Port: '$(bashio::services 'mqtt' 'port' 2>&1)'"
bashio::log.info "MQTT Username: '$(bashio::services 'mqtt' 'username' 2>&1)'"
bashio::log.info "MQTT Password: '$(bashio::services 'mqtt' 'password' 2>&1)'"

if bashio::config.is_empty 'mqtt' && bashio::var.has_value "$(bashio::services 'mqtt')"; then
    export MQTT_HOST="$(bashio::services 'mqtt' 'host')"
    export MQTT_PORT="$(bashio::services 'mqtt' 'port')"
    export MQTT_USERNAME="$(bashio::services 'mqtt' 'username')"
    export MQTT_PASSWORD="$(bashio::services 'mqtt' 'password')"
    bashio::log.info "Using Home Assistant MQTT service"
else 
    export MQTT_HOST=$(bashio::config 'mqtt.host')
    export MQTT_PORT=$(bashio::config 'mqtt.port')
    export MQTT_USERNAME=$(bashio::config 'mqtt.user')
    export MQTT_PASSWORD=$(bashio::config 'mqtt.pass')
    bashio::log.info "Using manual MQTT configuration"
fi

export DISCOVERY_TOPIC="homeassistant"

bashio::log.info "Final MQTT Configuration: ${MQTT_HOST}:${MQTT_PORT}"

# Start the Python application
exec python3 main.py
