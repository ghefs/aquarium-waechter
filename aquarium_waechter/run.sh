#!/usr/bin/with-contenv bashio
# MQTT-Zugang vom Supervisor (Mosquitto-Add-on)
if bashio::services.available mqtt; then
  export MQTT_HOST="$(bashio::services mqtt host)"
  export MQTT_PORT="$(bashio::services mqtt port)"
  export MQTT_USER="$(bashio::services mqtt username)"
  export MQTT_PASSWORD="$(bashio::services mqtt password)"
else
  bashio::log.warning "Kein MQTT-Broker gefunden – bitte das Mosquitto-Add-on installieren."
fi
# Einstellungen + Dashboard-Fingerabdruck in /config (= addon_configs/<slug>, ist im Backup)
exec python3 -u -m waechter /data/options.json --data /config
