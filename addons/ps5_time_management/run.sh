#!/usr/bin/env bashio

# Set up environment variables for MQTT
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

# Set up other environment variables
export DISCOVERY_TOPIC="$(bashio::services 'mqtt' 'discovery_topic' || echo 'homeassistant')"

bashio::log.info "MQTT Configuration: ${MQTT_HOST}:${MQTT_PORT}"

# Start the Python application
exec python3 main.py
