#!/usr/bin/env bashio

bashio::log.info "Starting PS5 Time Management add-on..."

# Debug: Check available environment variables
bashio::log.info "Checking environment variables..."
bashio::log.info "SUPERVISOR_TOKEN available: $([ -n "$SUPERVISOR_TOKEN" ] && echo "yes" || echo "no")"

# Try to get MQTT configuration from Supervisor API
bashio::log.info "Attempting to get MQTT configuration from Supervisor API..."

# Check if we can access the Supervisor API
if [ -n "$SUPERVISOR_TOKEN" ] && curl -s -H "Authorization: Bearer $SUPERVISOR_TOKEN" http://supervisor/services/mqtt > /dev/null 2>&1; then
    bashio::log.info "Supervisor API accessible, getting MQTT configuration..."
    
    # Get MQTT configuration from Supervisor API
    MQTT_CONFIG=$(curl -s -H "Authorization: Bearer $SUPERVISOR_TOKEN" http://supervisor/services/mqtt)
    
    if [ "$MQTT_CONFIG" != "null" ] && [ "$MQTT_CONFIG" != "" ]; then
        export MQTT_HOST=$(echo "$MQTT_CONFIG" | jq -r '.host // empty')
        export MQTT_PORT=$(echo "$MQTT_CONFIG" | jq -r '.port // empty')
        export MQTT_USERNAME=$(echo "$MQTT_CONFIG" | jq -r '.username // empty')
        export MQTT_PASSWORD=$(echo "$MQTT_CONFIG" | jq -r '.password // empty')
        
        bashio::log.info "Using Supervisor API MQTT configuration"
        bashio::log.info "MQTT Host: ${MQTT_HOST}"
        bashio::log.info "MQTT Port: ${MQTT_PORT}"
        bashio::log.info "MQTT Username: ${MQTT_USERNAME}"
    else
        bashio::log.warning "No MQTT configuration found in Supervisor API"
    fi
else
    bashio::log.warning "Cannot access Supervisor API (token: $([ -n "$SUPERVISOR_TOKEN" ] && echo "available" || echo "missing")), falling back to Bashio"
    
    # Fallback to Bashio method
    if bashio::config.is_empty 'mqtt' && bashio::var.has_value "$(bashio::services 'mqtt')"; then
        export MQTT_HOST="$(bashio::services 'mqtt' 'host')"
        export MQTT_PORT="$(bashio::services 'mqtt' 'port')"
        export MQTT_USERNAME="$(bashio::services 'mqtt' 'username')"
        export MQTT_PASSWORD="$(bashio::services 'mqtt' 'password')"
        bashio::log.info "Using Bashio MQTT service"
    else 
        export MQTT_HOST=$(bashio::config 'mqtt.host')
        export MQTT_PORT=$(bashio::config 'mqtt.port')
        export MQTT_USERNAME=$(bashio::config 'mqtt.user')
        export MQTT_PASSWORD=$(bashio::config 'mqtt.pass')
        bashio::log.info "Using manual MQTT configuration"
    fi
fi

# Set up other environment variables
export DISCOVERY_TOPIC="homeassistant"

bashio::log.info "Final MQTT Configuration: ${MQTT_HOST}:${MQTT_PORT}"

# Start the Python application
exec python3 main.py
