# PS5 Time Management

[![Open your Home Assistant instance and show the add-on repository.](https://my.home-assistant.io/badges/supervisor_add_addon_repository.svg)](https://my.home-assistant.io/redirect/supervisor_add_addon_repository/?repository_url=https%3A%2F%2Fgithub.com%2Fwillcm%2Fps5-time-management)

A comprehensive Home Assistant add-on for tracking and managing PlayStation 5 playtime with advanced parental controls and time limits.

## Features

### 📊 Time Tracking
- **Daily, Weekly, Monthly, Yearly** statistics per user
- **Per-game** time tracking with detailed breakdowns
- **Session history** with start/end times
- **Top games** reporting

### ⏱️ Time Limits & Controls
- **Configurable daily limits** per user
- **Automatic PS5 shutdown** when limits are reached
- **Graceful shutdown warnings** before time expires
- **Real-time limit enforcement**

### 👨‍👩‍👧 Parental Controls
- **User-based restrictions**
- **Usage reports** for parents
- **Notifications** for limit warnings and violations
- **Flexible scheduling** (coming soon)

### 📈 Reporting
- Comprehensive playtime reports
- Game-by-game statistics
- Historical data tracking
- Email/text notifications (configurable)

## Requirements

- Home Assistant (2023.1.0 or later)
- The [ps5-mqtt](https://github.com/FunkeyFlo/ps5-mqtt) add-on installed and configured
- MQTT broker (Mosquitto add-on recommended)

## 📦 Installation

### One-Click Install

Click the blue "ADD ADD-ON REPOSITORY TO MY HOME ASSISTANT" button above, or:

### Manual Installation

1. In Home Assistant, go to **Settings** → **Add-ons** → **Add-on Store**
2. Click the three dots menu (⋮) → **Repositories**
3. Add: `https://github.com/willcm/ps5-time-management`
4. Click **Add**
5. Find **PS5 Time Management** and click **Install**

### Via CLI

```bash
ha addons add-repository https://github.com/willcm/ps5-time-management
ha addons install ps5_time_management
```

## Configuration UI

The add-on includes a full configuration interface:

- **Access**: Settings → Add-ons → PS5 Time Management → Configuration
- **Settings**: All options are configurable via the UI
- **Log Window**: View real-time logs in the add-on Log tab
- **Log Levels**: Set `log_level` to DEBUG for full debug output
- **No Manual Edit Needed**: Everything configurable via Home Assistant UI

## Configuration

### Basic Configuration

**🎉 Automatic MQTT Configuration!** Just like ps5-mqtt, our add-on automatically connects to Home Assistant's MQTT broker with no configuration needed.

**Default Configuration (No Changes Needed):**
```json
{
  "mqtt": {},
  "mqtt_topic_prefix": "ps5",
  "enable_parental_controls": true,
  "enable_auto_shutdown": true,
  "default_daily_limit_minutes": 120,
  "log_level": "INFO"
}
```

**Manual MQTT Configuration (Optional):**
```json
{
  "mqtt": {
    "host": "192.168.1.100",
    "port": 1883,
    "user": "mqttuser",
    "pass": "password",
    "discovery_topic": "homeassistant"
  },
  "mqtt_topic_prefix": "ps5"
}
```

### Automatic User Discovery

**🎉 No manual user configuration needed!** The add-on automatically discovers users from ps5-mqtt:

- **Configuration Discovery**: Reads ps5-mqtt's configuration file to find PSN accounts
- **MQTT Discovery**: Monitors MQTT topics for user activity
- **Real-time Updates**: New users are discovered as they play games

### Setting User Limits

Use the Home Assistant UI to set time limits for discovered users:

1. Go to **Settings → Devices & Services → Helpers**
2. Find **PS5 User to Manage** and enter the username
3. Set **PS5 User Daily Limit** to desired minutes
4. Limits are applied automatically

Or use the API:
```bash
curl -X POST http://localhost:8080/api/limits/john \
  -H "Content-Type: application/json" \
  -d '{"daily_minutes": 60, "enabled": true}'
```

## Usage

### API Endpoints

The add-on provides a REST API for integration:

- `GET /api/health` - Health check
- `GET /api/users` - Get list of discovered users
- `GET /api/users/<user>/stats` - Get all stats for a user
- `GET /api/stats/daily/<user>` - Get daily playtime
- `GET /api/stats/weekly/<user>` - Get weekly playtime
- `GET /api/stats/monthly/<user>` - Get monthly playtime
- `GET /api/games/top/<user>` - Get top games (last 30 days)
- `GET /api/limits/<user>` - Get user's time limit
- `POST /api/limits/<user>` - Set user's time limit
- `GET /api/active_sessions` - Get all active gaming sessions
- `GET /api/notifications/<user>` - Get notifications for user
- `GET /api/report/<user>` - Generate comprehensive report

### Home Assistant Integration

#### Creating Sensors

Add to `configuration.yaml`:

```yaml
rest:
  - resource: http://localhost:8080/api/users
    scan_interval: 60
    sensor:
      - name: "PS5 Discovered Users"
        value_template: '{{ value_json.users | join(", ") }}'

template:
  - sensor:
      - name: "PS5 Users Count"
        state: >
          {% set users = states('sensor.ps5_discovered_users').split(', ') %}
          {{ users | select('string') | list | length }}
```

#### Displaying Statistics

Create a card in Lovelace:

```yaml
type: markdown
content: |
  # PS5 Time Management
  {% set minutes = states('sensor.kid1_daily_playtime') | int %}
  Daily playtime: {{ (minutes / 60) | int }}h {{ minutes % 60 }}m
```

### Example Automations

#### Warn Before Shutdown

```yaml
automation:
  - alias: "PS5 Time Warning"
    trigger:
      platform: time_pattern
      minutes: '/10'  # Every 10 minutes
    condition:
      condition: template
      value_template: "{{ states('sensor.kid1_time_remaining') | int < 10 }}"
    action:
      service: notify.mobile_app
      data:
        message: "Only {{ states('sensor.kid1_time_remaining') }} minutes remaining!"
```

#### Auto-Shutdown When Limit Reached

```yaml
automation:
  - alias: "PS5 Time Limit Reached"
    trigger:
      platform: numeric_state
      entity_id: sensor.kid1_time_remaining
      below: 0
    action:
      service: mqtt.publish
      data:
        topic: "ps5/[ps5_id]/command"
        payload: '{"action": "turn_off"}'
```

## Advanced Features

### Scheduled Downtime

Set specific hours when the PS5 should be unavailable:

```python
# Coming soon - weekly schedule
schedule = {
    'monday': {'enabled': True, 'hours': '18:00-21:00'},
    'tuesday': {'enabled': True, 'hours': '18:00-21:00'},
    # etc.
}
```

### Rewards System

Allow earning extra time through chores or achievements:

```python
# Example: Earn 30 minutes by completing chores
reward = {
    'description': 'Clean your room',
    'time_bonus_minutes': 30
}
```

### Usage Reports

Send weekly reports to parents:

```yaml
automation:
  - alias: "Weekly PS5 Report"
    trigger:
      platform: time
      at: '20:00:00'
      day_of_week: 'sun'
    action:
      service: http.get
      url: http://localhost:8080/api/report/kid1
      # Then send via email/notify
```

## Troubleshooting

### Add-on won't start
- Check logs: `http://your-ha-url:8123/supervisor/logs/ps5_time_management`
- Verify MQTT connection settings
- Ensure ps5-mqtt add-on is running

### No playtime data
- Verify ps5-mqtt is publishing to expected MQTT topics
- Check MQTT broker connection
- Review add-on logs for errors

### Auto-shutdown not working
- Enable `enable_auto_shutdown` in configuration
- Verify MQTT publish permissions
- Check that PS5 is controllable via MQTT

## Support

For issues, feature requests, or questions:
- GitHub Issues: [Your Repository]
- Home Assistant Community: [Community Link]

## License

MIT License - feel free to modify and distribute

## Credits

Built as a companion to [ps5-mqtt](https://github.com/FunkeyFlo/ps5-mqtt) by FunkeyFlo

