"""Constants for Dooya blinds."""

DOMAIN = "harf_dooya"
CONF_TRANSMITTER = "transmitter"
CONF_REMOTE_ID = "remote_id"
CONF_ENTITY_KEY = "entity_key"  # Stable HA device/entity identity.
CONF_CHANNELS = "channels"
CONF_TRAVEL_TIMES = "travel_times"

FREQUENCY = 433_890_000
MAX_REMOTE_ID = 0xFFFFFFF
MAX_CHANNELS = 15

BUTTON_OPEN = 1
BUTTON_CLOSE = 3
BUTTON_STOP = 5
BUTTON_PROGRAM = 0xC

CONF_RECEIVER = "receiver"
CONF_LEARN = "learn"
LEARN_TIMEOUT = 30
