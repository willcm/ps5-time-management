# Quick Start Guide - PS5 Time Management

## Installation (5 minutes)

### 1. Install Prerequisites

First, ensure you have these Home Assistant add-ons installed:
- **Mosquitto broker** (MQTT)
- **ps5-mqtt** add-on from https://github.com/FunkeyFlo/ps5-mqtt

### 2. Install PS5 Time Management Add-on

1. Copy this entire folder to your Home Assistant's `addons` directory:
   - On Home Assistant OS: `/config/addons/local/`
   - On Home Assistant Container: `/config/addons/ps5_time_management/`

2. Go to **Settings → Add-ons → Local Add-ons**
3. Find **PS5 Time Management** and click **Install**

### 3. Configure the Add-on

1. Click **Configuration** tab
2. Adjust settings:
   ```json
   {
     "mqtt_host": "core-mosquitto",
     "mqtt_port": 1883,
     "default_daily_limit_minutes": 120,
     "enable_auto_shutdown": true
   }
   ```
3. Click **Save**

### 4. Start the Add-on

1. Click **Start**
2. Check logs to verify it connected to MQTT

### 5. Set Up Users (Automatic!)

**🎉 No manual user setup needed!** The add-on automatically discovers users from ps5-mqtt:

1. **Automatic Discovery**: Users are discovered from:
   - ps5-mqtt configuration file
   - MQTT messages when users play games
   - Real-time user activity

2. **Set Time Limits**: Use Home Assistant UI:
   - Go to **Settings → Devices & Services → Helpers**
   - Find **PS5 User to Manage** and **PS5 User Daily Limit**
   - Enter username and set limit
   - Limits are applied automatically

3. **Check Discovered Users**:
   - View **PS5 Discovered Users** sensor to see all found users
   - View **PS5 Users Count** to see how many users are tracked

## Verification

### Test API

```bash
# Check health
curl http://localhost:8080/api/health

# Get daily stats (replace 'kid1' with your user)
curl http://localhost:8080/api/stats/daily/kid1

# Check active sessions
curl http://localhost:8080/api/active_sessions
```

### Test in Home Assistant

Add this to `configuration.yaml`:

```yaml
rest:
  - resource: "http://localhost:8080/api/stats/daily/kid1"
    scan_interval: 300
    sensor:
      - name: "PS5 Daily Time"
        unit_of_measurement: 'min'
        value_template: '{{ value_json.minutes }}'
```

Then reload YAML configuration.

## Next Steps

1. **Add sensors** - Copy `home-assistant-sensors.yaml` to your `configuration.yaml`
2. **Users auto-discovered** - No manual configuration needed!
3. **Set limits** - Use Home Assistant UI helpers to set time limits
4. **Add automations** - See `example-automations.yaml`
5. **Create dashboard** - See `example-lovelace-card.yaml`

## Troubleshooting

### Add-on won't start
- Check: `Settings → System → Logs`
- Verify MQTT connection in logs
- Ensure ps5-mqtt is running

### No data appearing
- Verify ps5-mqtt is publishing data
- Check MQTT topic prefix matches in both add-ons
- Review add-on logs

### API returns 404
- Verify add-on is running (Status should be "Running")
- Check port 8080 is accessible
- Try: `http://localhost:8080/api/health`

## Getting Help

- Check the main `README.md` for detailed documentation
- Review logs in Home Assistant
- Test MQTT topics with MQTT Explorer

## Optional: Add Web UI

Access the add-on's web interface at:
`http://your-ha-url:8123/hassio/ingress/ps5_time_management`

This provides a built-in dashboard for viewing stats and managing settings.

