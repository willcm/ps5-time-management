# PS5 Time Management

Track and manage PS5 playtime with user authentication, time limits, and parental controls.

## Features

- **Automatic User Discovery**: Automatically detects users from your existing PS5-MQTT setup
- **Comprehensive Time Tracking**: Daily, weekly, monthly, and yearly playtime statistics
- **Game-Specific Analytics**: Track time spent on individual games
- **Parental Controls**: Set daily time limits for each user
- **Auto-Shutdown**: Automatically turn off the PS5 when time limits are reached
- **Graceful Warnings**: Configurable warnings before shutdown
- **Real-time Monitoring**: Live playtime tracking via MQTT
- **Home Assistant Integration**: Native sensors, automations, and UI components

## Requirements

- Home Assistant 2023.1.0 or newer
- PS5-MQTT add-on installed and configured
- MQTT broker (Mosquitto recommended)

## Installation

1. Add this repository to Home Assistant
2. Install the PS5 Time Management add-on
3. Start the add-on
4. Configure your settings
5. Add the sensors to your configuration.yaml

## Configuration

The add-on automatically discovers users from your PS5-MQTT configuration and connects to your MQTT broker. No manual user setup required!

### Key Settings

- **Enable Parental Controls**: Turn on/off time limit enforcement
- **Default Daily Limit**: Default time limit in minutes (default: 120)
- **Auto-Shutdown**: Automatically turn off PS5 when limit reached
- **Graceful Warnings**: Show warnings before shutdown
- **Log Level**: Configure debug output (DEBUG, INFO, WARNING, ERROR)

## Support

For issues, feature requests, or questions:
- GitHub Issues: [willcm/ps5-time-management](https://github.com/willcm/ps5-time-management)

## License

MIT License - feel free to modify and distribute

## Credits

Built as a companion to [ps5-mqtt](https://github.com/FunkeyFlo/ps5-mqtt) by FunkeyFlo
