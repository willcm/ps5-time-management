# Quick Install Summary

## 📋 Requirements
- Home Assistant (2023.1.0+)
- ps5-mqtt add-on installed and configured
- MQTT broker running

## 🚀 Installation

### Option 1: Quick Install (Recommended)

1. **Copy folder to Home Assistant:**
   ```bash
   # SSH into your Home Assistant
   scp -r "PS5 Time Management" root@your-ha-ip:/config/addons/local/ps5_time_management
   ```

2. **Install via UI:**
   - Open Home Assistant → Settings → Add-ons
   - Click "Local Add-ons"
   - Find "PS5 Time Management"
   - Click "Install"
   - Click "Start"

3. **Check logs:**
   - Click on the add-on
   - Go to "Log" tab
   - Should see: "Starting Flask app on port 8080"

### Option 2: Manual Copy

1. **Locate your Home Assistant config directory:**
   ```
   /config/addons/local/
   ```

2. **Copy this entire folder there:**
   ```
   /config/addons/local/ps5_time_management/
   ```

3. **Install and start via Home Assistant UI**

## ⚙️ Configuration

**Access Configuration:**
- Settings → Add-ons → PS5 Time Management → Configuration

**Key Settings:**
```json
{
  "mqtt": {},                           // Automatic MQTT connection!
  "mqtt_topic_prefix": "ps5",          // Match ps5-mqtt topic prefix
  "log_level": "INFO",                 // Set to "DEBUG" for full debug
  "enable_auto_shutdown": true,        // Auto turn off PS5
  "default_daily_limit_minutes": 120    // Default time limit
}
```

**🎉 MQTT is Automatic!** No MQTT configuration needed - just like ps5-mqtt!

**Enable Full Debug Output:**
1. Set `log_level` to `DEBUG`
2. Restart add-on
3. View Log tab for complete output

## 🔍 Verification

**Test API:**
```bash
# Health check
curl http://localhost:8080/api/health
# Expected: {"status":"ok"}

# User discovery
curl http://localhost:8080/api/users
# Expected: {"users":[...], "count":X}
```

**Check Logs:**
- Click add-on → Log tab
- Should see MQTT connection, user discovery, and Flask startup

**View in Home Assistant:**
- Check for add-on in Settings → Add-ons
- Status should be "Running"

## 📝 Next Steps

1. **Add Sensors** - Copy `home-assistant-sensors.yaml` to your config
2. **Set Limits** - Configure time limits via Home Assistant UI
3. **Create Dashboard** - Use examples in `example-lovelace-card.yaml`
4. **Add Automations** - Use examples in `example-automations.yaml`

## 🆘 Troubleshooting

**Can't See Add-on?**
- Make sure folder is in `/config/addons/local/`
- Folder name must be `ps5_time_management`
- Restart Home Assistant

**Add-on Won't Start?**
- Check Log tab for errors
- Verify MQTT broker is running
- Check configuration syntax

**No Data?**
- Verify ps5-mqtt is running
- Set log_level to DEBUG
- Check MQTT topics match

**Need More Help?**
- Enable DEBUG logging
- Check full installation guide: [INSTALLATION.md](INSTALLATION.md)
- Review logs in Home Assistant

## 📍 Key Features

✅ **Automatic User Discovery** - No manual configuration needed
✅ **Configurable Logging** - Set to DEBUG for full output
✅ **Real-time Logs** - View in Home Assistant UI
✅ **Full Debug Mode** - See all operations and MQTT messages
✅ **Settings UI** - Configure everything via Home Assistant
✅ **No External Scripts** - Everything managed within Home Assistant

