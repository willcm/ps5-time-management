# Installation Guide - PS5 Time Management

## 📦 Installation Steps

### Step 1: Copy Add-on to Home Assistant

Copy this entire folder to your Home Assistant add-ons directory:

**For Home Assistant OS/Supervisor:**
```bash
# SSH into your Home Assistant
ssh homeassistant@your-ha-ip

# Create local add-ons directory (if it doesn't exist)
mkdir -p /config/addons/local

# Copy the PS5 Time Management folder
# From your computer, use SFTP or SCP to upload the folder
```

**Or manually via SSH:**
```bash
# On your computer, upload the folder
scp -r "PS5 Time Management" root@your-ha-ip:/config/addons/local/ps5_time_management
```

**For Home Assistant Container:**
```bash
# Copy to your config directory
cp -r "PS5 Time Management" /path/to/your/config/addons/ps5_time_management
```

### Step 2: Install via Supervisor

1. Open Home Assistant web interface
2. Go to **Settings → Add-ons**
3. Click **Local Add-ons** (or scroll down if it's the only local add-on)
4. Find **PS5 Time Management**
5. Click **Install**
6. Wait for installation to complete

### Step 3: Configure the Add-on

1. Click **Open Web UI** to access configuration
2. Or configure via **Settings → Add-ons → PS5 Time Management → Configuration**

**Default Configuration (No MQTT Setup Needed!):**
```json
{
  "mqtt": {},
  "mqtt_topic_prefix": "ps5",
  "database_path": "/data/ps5_time_management.db",
  "enable_parental_controls": true,
  "enable_auto_shutdown": true,
  "default_daily_limit_minutes": 120,
  "graceful_shutdown_enabled": true,
  "graceful_shutdown_warnings": true,
  "warning_before_shutdown_minutes": 10,
  "log_level": "INFO"
}
```

**🎉 MQTT Configuration is Automatic!** 
- The add-on automatically connects to Home Assistant's MQTT broker
- No manual MQTT configuration needed (just like ps5-mqtt)
- Manual configuration available if needed

### Step 4: Start the Add-on

1. Go to **Settings → Add-ons → PS5 Time Management**
2. Click **Start**
3. Check the **Log** tab to verify it's running

### Step 5: Verify Installation

**Check Logs:**
1. Click on the add-on
2. Go to **Log** tab
3. You should see:
   ```
   2024-01-XX XX:XX:XX - INFO - Configuration loaded from /data/options.json
   2024-01-XX XX:XX:XX - INFO - Connected to MQTT broker successfully
   2024-01-XX XX:XX:XX - INFO - Starting Flask app on port 8080
   ```

**Check API:**
```bash
# Test health endpoint
curl http://localhost:8080/api/health

# Should return: {"status":"ok"}

# Test user discovery
curl http://localhost:8080/api/users

# Should return: {"users":[...], "count":X}
```

## 🔧 Configuration Options

### Configuration Page

Access the configuration via:
- **Home Assistant UI**: Settings → Add-ons → PS5 Time Management → Configuration
- **Web Interface**: http://your-ha-url:8123/hassio/ingress/ps5_time_management

### Logging Levels

**Available log levels:**
- `DEBUG` - Full debug output with all details
- `INFO` - Standard information (default)
- `WARNING` - Warnings only
- `ERROR` - Errors only

**To change log level:**
1. Go to add-on Configuration
2. Set `log_level` to `DEBUG` for full debug output
3. Click **Save**
4. Restart the add-on

### Log Window

**View Logs:**
- **Home Assistant UI**: Click add-on → **Log** tab
- **Command Line**: Check add-on logs via SSH
- **Real-time**: Logs update in real-time in the UI

**Full Debug Mode:**
Set `log_level` to `DEBUG` in configuration to see:
- All MQTT messages received
- Database operations
- API requests
- Session tracking
- Timer checks
- User discovery
- All configuration details

### Example Log Output:

**INFO Level (Default):**
```
11:23:45 - INFO - Configuration loaded from /data/options.json
11:23:45 - INFO - Starting Flask app on port 8080
11:23:46 - INFO - Connected to MQTT broker at core-mosquitto:1883
11:23:46 - INFO - Subscribed to MQTT topics with prefix: ps5
```

**DEBUG Level (Full Output):**
```
11:23:45 - DEBUG - Loading configuration from /data/options.json
11:23:45 - DEBUG - Full configuration: {
  "mqtt_host": "core-mosquitto",
  "mqtt_port": 1883,
  ...
}
11:23:45 - INFO - Configuration loaded from /data/options.json
11:23:46 - DEBUG - Connecting to MQTT broker at core-mosquitto:1883
11:23:46 - DEBUG - MQTT connection successful
11:23:46 - INFO - Connected to MQTT broker at core-mosquitto:1883
11:23:46 - DEBUG - Subscribing to topics: ps5/+/state, ps5/+/game, ...
11:23:46 - INFO - Subscribed to MQTT topics with prefix: ps5
11:23:47 - DEBUG - Received MQTT message on ps5/device_1/user: {"user": "john"}
11:23:47 - INFO - Discovered new user from MQTT: john
```

## 🎯 Next Steps

1. **Add Home Assistant Sensors** - Copy content from `home-assistant-sensors.yaml` to your `configuration.yaml`
2. **Set Up Users** - Users are discovered automatically from ps5-mqtt
3. **Configure Limits** - Use Home Assistant UI to set time limits
4. **Create Dashboard** - Use examples from `example-lovelace-card.yaml`

## 🔍 Troubleshooting

### Add-on Won't Start
- Check logs for error messages
- Verify MQTT broker is running
- Check configuration syntax

### No Data Appearing
- Verify ps5-mqtt is running and publishing data
- Check MQTT topic prefix matches
- Enable DEBUG logging to see MQTT messages

### Can't Access API
- Verify add-on is running (Status should be "Running")
- Check port 8080 is accessible
- Try: `curl http://localhost:8080/api/health`

### Need Help?
- Enable DEBUG logging to see full output
- Check Home Assistant system logs
- Review MQTT broker logs

