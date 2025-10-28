#!/usr/bin/env bashio

bashio::log.info "Starting PS5 Time Management add-on..."

# Debug: Test Bashio service calls
bashio::log.info "Testing Bashio service calls..."

# Test if we can call bashio::services at all
bashio::log.info "Testing bashio::services 'mqtt' call..."
MQTT_SERVICE_RESULT=$(bashio::services 'mqtt' 2>&1)
bashio::log.info "bashio::services 'mqtt' result: '${MQTT_SERVICE_RESULT}'"

# Test if we can call bashio::var.has_value
bashio::log.info "Testing bashio::var.has_value..."
if bashio::var.has_value "$(bashio::services 'mqtt')"; then
    bashio::log.info "bashio::var.has_value returned TRUE"
else
    bashio::log.info "bashio::var.has_value returned FALSE"
fi

# Test if we can call bashio::config.is_empty
bashio::log.info "Testing bashio::config.is_empty 'mqtt'..."
if bashio::config.is_empty 'mqtt'; then
    bashio::log.info "bashio::config.is_empty 'mqtt' returned TRUE (mqtt config is empty)"
else
    bashio::log.info "bashio::config.is_empty 'mqtt' returned FALSE (mqtt config has values)"
fi

# Test individual service calls
bashio::log.info "Testing individual MQTT service calls..."
bashio::log.info "MQTT Host: '$(bashio::services 'mqtt' 'host' 2>&1)'"
bashio::log.info "MQTT Port: '$(bashio::services 'mqtt' 'port' 2>&1)'"
bashio::log.info "MQTT Username: '$(bashio::services 'mqtt' 'username' 2>&1)'"
bashio::log.info "MQTT Password: '$(bashio::services 'mqtt' 'password' 2>&1)'"

# Use the exact same approach as ps5-mqtt
bashio::log.info "Applying ps5-mqtt logic..."
if bashio::config.is_empty 'mqtt' && bashio::var.has_value "$(bashio::services 'mqtt')"; then
    bashio::log.info "Condition TRUE: Using Home Assistant MQTT service"
    export MQTT_HOST="$(bashio::services 'mqtt' 'host')"
    export MQTT_PORT="$(bashio::services 'mqtt' 'port')"
    export MQTT_USERNAME="$(bashio::services 'mqtt' 'username')"
    export MQTT_PASSWORD="$(bashio::services 'mqtt' 'password')"
    bashio::log.info "Using Home Assistant MQTT service"
    bashio::log.info "MQTT Host: ${MQTT_HOST}"
    bashio::log.info "MQTT Port: ${MQTT_PORT}"
    bashio::log.info "MQTT Username: ${MQTT_USERNAME}"
else 
    bashio::log.info "Condition FALSE: Using manual MQTT configuration"
    export MQTT_HOST=$(bashio::config 'mqtt.host')
    export MQTT_PORT=$(bashio::config 'mqtt.port')
    export MQTT_USERNAME=$(bashio::config 'mqtt.user')
    export MQTT_PASSWORD=$(bashio::config 'mqtt.pass')
    bashio::log.info "Using manual MQTT configuration"
fi

# Set up other environment variables
export DISCOVERY_TOPIC="homeassistant"

bashio::log.info "Final MQTT Configuration: ${MQTT_HOST}:${MQTT_PORT}"

# Start the Python application
exec python3 main.py
