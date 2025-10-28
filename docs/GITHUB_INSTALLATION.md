# Installation via GitHub Repository

## Method 1: Add Repository to Home Assistant

1. In Home Assistant, go to **Settings** → **Add-ons** → **Add-on Store**
2. Click the three dots menu (⋮) in the top right corner
3. Select **Repositories**
4. Add this repository URL: `https://github.com/willcm/ps5-time-management`
5. Click **Add**
6. Wait for the repository to load
7. Find **PS5 Time Management** in the list and click **Install**

## Method 2: Manual Repository Addition

If the above doesn't work, you can add the repository manually:

1. Go to **Settings** → **Add-ons** → **Add-on Store**
2. Click the three dots menu (⋮) → **Repositories**
3. Add: `https://github.com/willcm/ps5-time-management`
4. Click **Add**

## Method 3: Using Home Assistant CLI

```bash
# Add the repository
ha addons add-repository https://github.com/willcm/ps5-time-management

# Install the add-on
ha addons install ps5_time_management
```

## Post-Installation Setup

1. **Start the add-on** from the Add-on Store
2. **Configure** the add-on with your settings
3. **Add the sensors** to your `configuration.yaml` (see `home-assistant-sensors.yaml`)
4. **Restart Home Assistant** to load the new sensors

## Troubleshooting

- If the add-on doesn't appear, try refreshing the Add-on Store page
- Check that your Home Assistant version is 2023.1.0 or newer
- Ensure you have the required services (MQTT broker) running
