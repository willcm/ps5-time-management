# PS5 Time Management

[![Open your Home Assistant instance and show the add-on repository.](https://my.home-assistant.io/badges/supervisor_add_addon_repository.svg)](https://my.home-assistant.io/redirect/supervisor_add_addon_repository/?repository_url=https%3A%2F%2Fgithub.com%2Fwillcm%2Fps5-time-management)

A comprehensive Home Assistant add-on for tracking and managing PlayStation 5 playtime with advanced parental controls and time limits.

## Support This Project

If you find this add-on useful, please consider supporting its development!

Like many parents, my coding time happens in the early morning hours before the kids wake up - and let's be honest, that 5 AM coffee is essential for making this code actually work! 😴☕

[![Buy Me A Coffee](https://img.shields.io/badge/Buy%20Me%20A%20Coffee-FFDD00?style=for-the-badge&logo=buy-me-a-coffee&logoColor=black)](https://buymeacoffee.com/willcm)

Your support helps keep the coffee flowing and the code improving!

## Features

### Time Tracking
- **Rolling time periods** - "Today", "Last 7 Days", "Last 30 Days" for more accurate reporting
- **Daily, Weekly, Monthly, Yearly** statistics per user
- **Per-game** time tracking with detailed breakdowns
- **Session history** with start/end times
- **Top games** reporting
- **Real-time updates** - Statistics refresh automatically without page reload

### Time Limits & Controls
- **Per-day limits** - Set different time limits for each day of the week (Monday-Sunday)
- **Configurable daily limits** per user with flexible scheduling
- **Automatic PS5 shutdown** when limits are reached
- **Configurable shutdown warnings** before time expires
- **Immediate shutdown** for 0-minute days
- **Real-time limit enforcement**

### Parental Controls
- **PIN-protected admin area** - Secure admin interface with 4-digit PIN
- **User-based restrictions** with per-day scheduling
- **Usage reports** for parents
- **Notifications** for limit warnings and violations
- **Flexible weekly scheduling** - Different limits for each day of the week
- **Global settings management** - Configure default limits and warning times from admin UI

### Reporting
- Comprehensive playtime reports
- Game-by-game statistics
- Historical data tracking
- Email/text notifications (configurable)

## Requirements

- Home Assistant (2023.1.0 or later)
- The [ps5-mqtt](https://github.com/FunkeyFlo/ps5-mqtt) add-on installed and configured
- MQTT broker (Mosquitto add-on recommended)

## Installation

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

The add-on includes a comprehensive web interface and admin area:

### Web Interface
- **Access**: Open the add-on in Home Assistant (it will open in a new tab)
- **Real-time statistics** - View playtime, remaining time, and game breakdowns
- **User management** - View all discovered users and their stats
- **Auto-refresh** - Statistics update every 5 seconds automatically

### Admin Area (PIN Protected)
- **Access**: Click "Admin" link in the top-right of the web interface
- **PIN Protection**: 4-digit PIN (configured in add-on settings, default: 0000)
- **Per-day limits**: Set different time limits for each day of the week (0 minutes = no play)
- **Global settings**: Configure default daily limit and shutdown warning time
- **Enable/disable auto shutdown**: Toggle automatic PS5 shutdown feature
- **Clear all stats**: Reset all user statistics

### Home Assistant Configuration
- **Access**: Settings → Add-ons → PS5 Time Management → Configuration
- **Settings**: MQTT, database path, admin PIN, and log level
- **Log Window**: View real-time logs in the add-on Log tab
- **Log Levels**: Set `log_level` to DEBUG for full debug output

## Configuration

### Basic Configuration

**Automatic MQTT Configuration!** Just like ps5-mqtt, our add-on automatically connects to Home Assistant's MQTT broker with no configuration needed.

**Default Configuration (No Changes Needed):**
```json
{
  "mqtt": {},
  "mqtt_topic_prefix": "ps5-mqtt",
  "admin_pin": "0000",
  "log_level": "INFO"
}
```

**Note**: `default_daily_limit_minutes` and `warning_before_shutdown_minutes` are now managed via the Admin UI on the web interface. These settings are stored in the database and can be changed without restarting the add-on.

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

**No manual user configuration needed!** The add-on automatically discovers users from ps5-mqtt:

- **Configuration Discovery**: Reads ps5-mqtt's configuration file to find PSN accounts
- **MQTT Discovery**: Monitors MQTT topics for user activity
- **Real-time Updates**: New users are discovered as they play games

### Setting User Limits

**Recommended: Use the Admin Web Interface**

1. Open the PS5 Time Management add-on in Home Assistant
2. Click **Admin** in the top-right corner
3. Enter your 4-digit PIN (default: 0000, set in add-on configuration)
4. For each discovered user:
   - Set time limits for each day of the week (Monday-Sunday)
   - Enter minutes (0 = no play allowed on that day)
   - Click **Save Changes** (button appears when changes are made)
5. Configure global settings:
   - **Default Daily Time Limit**: Used when no per-day limit is set
   - **Shutdown Warning Time**: Minutes before shutdown when warning appears
   - **Enable Auto Shutdown**: Toggle automatic PS5 shutdown

**Using the API:**
```bash
# Set per-day limits for a user
curl -X POST http://localhost:8080/api/admin/limits/john \
  -H "Content-Type: application/json" \
  -d '{
    "limits": {
      "monday": 120,
      "tuesday": 120,
      "wednesday": 60,
      "thursday": 120,
      "friday": 120,
      "saturday": 180,
      "sunday": 180
    }
  }'

# Set global settings
curl -X POST http://localhost:8080/api/admin/settings \
  -H "Content-Type: application/json" \
  -d '{
    "default_daily_limit_minutes": 120,
    "warning_before_shutdown_minutes": 1,
    "enable_auto_shutdown": true
  }'
```

## Usage

### API Endpoints

The add-on provides a REST API for integration:

**Public Endpoints:**
- `GET /api/health` - Health check
- `GET /api/users` - Get list of discovered users
- `GET /api/users/<user>/stats` - Get all stats for a user (including remaining time)
- `GET /api/stats/daily/<user>` - Get daily playtime
- `GET /api/stats/weekly/<user>` - Get weekly playtime
- `GET /api/stats/monthly/<user>` - Get monthly playtime
- `GET /api/games/top/<user>` - Get top games (last 30 days)
- `GET /api/limits/<user>` - Get user's current day limit
- `GET /api/active_sessions` - Get all active gaming sessions
- `GET /api/notifications/<user>` - Get notifications for user
- `GET /api/report/<user>` - Generate comprehensive report

**Admin Endpoints (PIN protected):**
- `POST /api/admin/verify-pin` - Verify admin PIN
- `GET /api/admin/users` - Get list of discovered users (sorted)
- `GET /api/admin/limits/<user>` - Get per-day limits for a user
- `POST /api/admin/limits/<user>` - Set per-day limits for a user
- `GET /api/admin/settings` - Get global settings
- `POST /api/admin/settings` - Update global settings

### Home Assistant Integration

#### Auto-Created Sensors (MQTT Discovery)

**Sensors are automatically created via MQTT Discovery!** No manual configuration needed.

When the add-on discovers a user, it automatically creates the following sensors for each user via MQTT Discovery:

**For each user (e.g., "John"):**
- `sensor.ps5_john_daily_playtime` - Daily playtime in minutes
- `sensor.ps5_john_weekly_playtime` - Weekly playtime in minutes  
- `sensor.ps5_john_monthly_playtime` - Monthly playtime in minutes
- `sensor.ps5_john_time_remaining` - Time remaining today in minutes
- `sensor.ps5_john_current_game` - Currently playing game
- `sensor.ps5_john_session_active` - Whether session is active (ON/OFF)
- `binary_sensor.ps5_john_shutdown_warning` - Shutdown warning active (ON/OFF)

**All sensors are grouped under a device:** `PS5 Time Management - [username]`

**Sensor Creation:**
- Sensors are automatically created when users are discovered
- Sensors appear in Home Assistant within a few seconds
- No restart or manual configuration required
- Sensors update automatically as playtime changes

#### Displaying Statistics

Create a card in Lovelace:

```yaml
type: markdown
content: |
  # PS5 Time Management - John
  **Daily Playtime:** {{ states('sensor.ps5_john_daily_playtime') }} minutes
  **Time Remaining:** {{ states('sensor.ps5_john_time_remaining') }} minutes
  **Current Game:** {{ states('sensor.ps5_john_current_game') }}
  **Session Active:** {{ states('sensor.ps5_john_session_active') }}
```

Or use entity cards:

```yaml
type: entities
entities:
  - entity: sensor.ps5_john_daily_playtime
  - entity: sensor.ps5_john_time_remaining
  - entity: sensor.ps5_john_current_game
  - entity: sensor.ps5_john_session_active
```

### Example Automations

#### Warn Before Shutdown

```yaml
automation:
  - alias: "PS5 Time Warning"
    trigger:
      platform: state
      entity_id: binary_sensor.ps5_john_shutdown_warning
      to: "on"
    action:
      service: notify.mobile_app
      data:
        message: "PS5 time limit warning! Only {{ states('sensor.ps5_john_time_remaining') }} minutes remaining!"
```

#### Notification When Time Remaining is Low

```yaml
automation:
  - alias: "PS5 Low Time Warning"
    trigger:
      platform: numeric_state
      entity_id: sensor.ps5_john_time_remaining
      below: 10
    condition:
      condition: template
      value_template: "{{ states('sensor.ps5_john_session_active') == 'ON' }}"
    action:
      service: notify.mobile_app
      data:
        message: "Only {{ states('sensor.ps5_john_time_remaining') }} minutes remaining!"
```

#### Auto-Shutdown When Limit Reached

```yaml
automation:
  - alias: "PS5 Time Limit Reached"
    trigger:
      platform: numeric_state
      entity_id: sensor.ps5_john_time_remaining
      below: 1
    condition:
      condition: template
      value_template: "{{ states('sensor.ps5_john_session_active') == 'ON' }}"
    action:
      service: notify.mobile_app
      data:
        message: "PS5 time limit reached - shutdown triggered"
```

**Note:** The add-on automatically handles shutdowns when limits are reached (if auto-shutdown is enabled). This automation is for notifications only.

## Troubleshooting

### Add-on won't start
- Check logs: `http://your-ha-url:8123/supervisor/logs/ps5_time_management`
- Verify MQTT connection settings
- Ensure ps5-mqtt add-on is running

### No playtime data
- Verify ps5-mqtt is publishing to expected MQTT topics
- Check MQTT broker connection
- Review add-on logs for errors
- Check that users have been discovered (view in web interface)

### Sensors not appearing in Home Assistant
- Sensors are auto-created via MQTT Discovery - no manual configuration needed
- Check that MQTT Discovery is enabled in Home Assistant (Settings → Devices & Services → MQTT → Configure)
- Verify add-on is connected to MQTT broker (check add-on logs)
- Wait a few seconds after user discovery - sensors appear automatically
- Check MQTT Discovery topic matches (default: `homeassistant`)
- Restart Home Assistant if sensors still don't appear

### Auto-shutdown not working
- Enable `enable_auto_shutdown` in the Admin UI (Settings → Enable Auto Shutdown of PS5)
- Verify MQTT publish permissions
- Check that PS5 is controllable via MQTT
- Check logs for shutdown policy messages

### Admin PIN not working
- Default PIN is `0000` - change it in add-on configuration
- PIN is 4 digits only
- Clear browser cache if PIN changes don't take effect

### Per-day limits not applying
- Verify limits are saved in Admin UI (green save button should appear when changed)
- Check that the correct day's limit is being used (limits apply to the current day)
- Review logs for limit enforcement messages

## Support

For issues, feature requests, or questions:
- GitHub Issues: https://github.com/willcm/ps5-time-management

## License

MIT License - feel free to modify and distribute

## Credits

Built as a companion to [ps5-mqtt](https://github.com/FunkeyFlo/ps5-mqtt) by FunkeyFlo

