#!/usr/bin/env bashio

bashio::log.info "Starting PS5 Time Management add-on..."

# Try to get MQTT service configuration
bashio::log.info "Attempting to get MQTT service configuration..."

# Check if MQTT service is available
if bashio::var.has_value "$(bashio::services 'mqtt')"; then
    bashio::log.info "MQTT service is available, getting configuration..."
    
    # Get MQTT configuration from service
    export MQTT_HOST="$(bashio::services 'mqtt' 'host')"
    export MQTT_PORT="$(bashio::services 'mqtt' 'port')"
    export MQTT_USERNAME="$(bashio::services 'mqtt' 'username')"
    export MQTT_PASSWORD="$(bashio::services 'mqtt' 'password')"
    
    bashio::log.info "Using Home Assistant MQTT service"
    bashio::log.info "MQTT Host: ${MQTT_HOST}"
    bashio::log.info "MQTT Port: ${MQTT_PORT}"
    bashio::log.info "MQTT Username: ${MQTT_USERNAME}"
else
    bashio::log.warning "MQTT service not available, checking manual configuration..."
    
    # Check if manual MQTT configuration is provided
    if ! bashio::config.is_empty 'mqtt'; then
        export MQTT_HOST=$(bashio::config 'mqtt.host')
        export MQTT_PORT=$(bashio::config 'mqtt.port')
        export MQTT_USERNAME=$(bashio::config 'mqtt.user')
        export MQTT_PASSWORD=$(bashio::config 'mqtt.pass')
        bashio::log.info "Using manual MQTT configuration"
    else
        bashio::log.warning "No MQTT configuration provided, using defaults"
        export MQTT_HOST="core-mosquitto"
        export MQTT_PORT="1883"
        export MQTT_USERNAME=""
        export MQTT_PASSWORD=""
    fi
fi

# Set up other environment variables
export DISCOVERY_TOPIC="homeassistant"

bashio::log.info "Final MQTT Configuration: ${MQTT_HOST}:${MQTT_PORT}"

# Start the Python application
exec python3 main.py
