# PS5 Time Management - Kid Template Generator

# To add more kids, copy these blocks and replace "kid1" with your kid's name
# Then add the corresponding sections to your configuration.yaml

# Example: Adding "kid3" - copy these blocks:

# REST sensors for Kid 3:
# - resource: "http://localhost:8080/api/stats/daily/kid3"
#   scan_interval: 300
#   sensor:
#     - name: "PS5 Kid3 Daily Playtime"
#       unit_of_measurement: 'min'
#       value_template: '{{ value_json.minutes }}'
#       icon: 'mdi:playstation'
#       
# - resource: "http://localhost:8080/api/stats/weekly/kid3"
#   scan_interval: 3600
#   sensor:
#     - name: "PS5 Kid3 Weekly Playtime"
#       unit_of_measurement: 'min'
#       value_template: '{{ value_json.minutes }}'
#       icon: 'mdi:calendar-week'
#       
# - resource: "http://localhost:8080/api/stats/monthly/kid3"
#   scan_interval: 3600
#   sensor:
#     - name: "PS5 Kid3 Monthly Playtime"
#       unit_of_measurement: 'min'
#       value_template: '{{ value_json.minutes }}'
#       icon: 'mdi:calendar-month'
#       
# - resource: "http://localhost:8080/api/limits/kid3"
#   scan_interval: 300
#   sensor:
#     - name: "PS5 Kid3 Time Limit"
#       unit_of_measurement: 'min'
#       value_template: '{{ value_json.daily_limit }}'
#       icon: 'mdi:timer-outline'
#       
#     - name: "PS5 Kid3 Time Remaining"
#       unit_of_measurement: 'min'
#       value_template: '{{ value_json.remaining }}'
#       icon: 'mdi:timer'
#       
#     - name: "PS5 Kid3 Time Used"
#       unit_of_measurement: 'min'
#       value_template: '{{ value_json.current_time }}'
#       icon: 'mdi:clock-outline'

# Template sensors for Kid 3:
# - name: "PS5 Kid3 Daily Playtime Formatted"
#   state: >
#     {% set minutes = states('sensor.ps5_kid3_daily_playtime') | int %}
#     {{ (minutes / 60) | int }}h {{ minutes % 60 }}m
#     
# - name: "PS5 Kid3 Weekly Playtime Formatted"
#   state: >
#     {% set minutes = states('sensor.ps5_kid3_weekly_playtime') | int %}
#     {{ (minutes / 60) | int }}h {{ minutes % 60 }}m
#     
# - name: "PS5 Kid3 Time Remaining Formatted"
#   state: >
#     {% set minutes = states('sensor.ps5_kid3_time_remaining') | int %}
#     {{ minutes // 60 }}h {{ minutes % 60 }}m
#   icon: >
#     {% if states('sensor.ps5_kid3_time_remaining') | int < 10 %}
#       mdi:timer-alert
#     {% else %}
#       mdi:timer
#     {% endif %}

# REST command for Kid 3:
# set_kid3_limit:
#   url: "http://localhost:8080/api/limits/kid3"
#   method: "POST"
#   headers:
#     Content-Type: "application/json"
#   payload: '{"daily_minutes": {{ minutes }}, "enabled": true}'

# Input number for Kid 3:
# ps5_kid3_daily_limit:
#   name: "PS5 Kid3 Daily Limit"
#   min: 0
#   max: 480
#   step: 15
#   unit_of_measurement: 'min'
#   initial: 120

# Automation for Kid 3:
# - alias: "PS5 Update Kid3 Limit"
#   trigger:
#     - platform: state
#       entity_id: input_number.ps5_kid3_daily_limit
#   action:
#     - service: rest_command.set_kid3_limit
#       data:
#         minutes: "{{ states('input_number.ps5_kid3_daily_limit') | int }}"
#     - service: notify.persistent_notification
#       data:
#         message: "Updated PS5 time limit for Kid3 to {{ states('input_number.ps5_kid3_daily_limit') }} minutes"

# Instructions:
# 1. Copy the REST sensor blocks above and paste them in the 'rest:' section
# 2. Copy the template sensor blocks and paste them in the 'template:' section  
# 3. Copy the REST command block and paste it in the 'rest_command:' section
# 4. Copy the input_number block and paste it in the 'input_number:' section
# 5. Copy the automation block and paste it in the 'automation:' section
# 6. Replace "kid3" with your actual kid's username from ps5-mqtt
# 7. Replace "Kid3" with your kid's display name
# 8. Reload your YAML configuration

