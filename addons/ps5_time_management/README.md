# PS5 Time Management

Track and manage PS5 playtime with user authentication, time limits, and parental controls.

## Support This Project

If you find this add-on useful, please consider supporting its development!

Like many parents, my coding time happens in the early morning hours before the kids wake up - and let's be honest, that 5 AM coffee is essential for making this code actually work! 😴☕

[![Buy Me A Coffee](https://img.shields.io/badge/Buy%20Me%20A%20Coffee-FFDD00?style=for-the-badge&logo=buy-me-a-coffee&logoColor=black)](https://buymeacoffee.com/willcm)

Your support helps keep the coffee flowing and the code improving!

## Features

- **Automatic User Discovery**: Automatically detects users from your existing PS5-MQTT setup
- **Comprehensive Time Tracking**: Daily, weekly, monthly, and yearly statistics with rolling time periods
- **Per-Day Limits**: Set different time limits for each day of the week (Monday-Sunday)
- **Game-Specific Analytics**: Track time spent on individual games
- **PIN-Protected Admin Area**: Secure admin interface for managing limits and settings
- **Auto-Shutdown**: Automatically turn off the PS5 when time limits are reached
- **Configurable Warnings**: Customizable warnings before shutdown
- **Real-time Monitoring**: Live playtime tracking via MQTT
- **Auto-Created Sensors**: Sensors automatically created via MQTT Discovery - no manual configuration needed

## Requirements

- Home Assistant 2023.1.0 or newer
- PS5-MQTT add-on installed and configured
- MQTT broker (Mosquitto recommended)

## Installation

1. Add this repository to Home Assistant
2. Install the PS5 Time Management add-on
3. Start the add-on
4. Configure your settings (admin PIN, MQTT topic prefix, etc.)

## Configuration

The add-on automatically discovers users from your PS5-MQTT configuration and connects to your MQTT broker. No manual user setup required!

### Key Settings

- **Admin PIN**: 4-digit PIN for accessing the admin area (default: 0000)
- **MQTT Topic Prefix**: Topic prefix for ps5-mqtt (default: ps5-mqtt)
- **Log Level**: Configure debug output (DEBUG, INFO, WARNING, ERROR)

### Managing Settings

Most settings (default daily limit, shutdown warning time, auto-shutdown) are managed via the Admin UI on the web interface, not in the configuration file.

## Support

For issues, feature requests, or questions:
- GitHub Issues: [willcm/ps5-time-management](https://github.com/willcm/ps5-time-management)

## License

MIT License - feel free to modify and distribute

## Credits

Built as a companion to [ps5-mqtt](https://github.com/FunkeyFlo/ps5-mqtt) by FunkeyFlo
