"""Constants for the Stays.net integration."""

from __future__ import annotations

DOMAIN = "staysnet"

CONF_USERNAME = "username"
CONF_PASSWORD = "password"
CONF_SYSTEM_ID = "system_id"

# Options
CONF_PROPERTIES = "properties"
CONF_PROPERTY_NAMES = "property_names"
CONF_PROPERTY_CODES = "property_codes"
CONF_SCAN_INTERVAL = "scan_interval"
CONF_DAYS_AHEAD = "days_ahead"
CONF_CHECKIN_TIME = "checkin_time"
CONF_CHECKOUT_TIME = "checkout_time"

DEFAULT_SCAN_INTERVAL = 15  # minutes
DEFAULT_DAYS_AHEAD = 90  # max 330 (options flow)
DEFAULT_CHECKIN_TIME = "14:00:00"
DEFAULT_CHECKOUT_TIME = "11:00:00"

BASE_URL_TEMPLATE = "https://{system_id}.stays.net"
LOGIN_URL = "https://my.stays.net/restapi/undefined/users/findMyStays"

# Calendar entry types that are NOT stays. Everything else (booked, reserved,
# contract, altered, ...) is a reservation, as in stays-sync.
NON_STAY_TYPES = {"blocked", "maintenance", "cancelled", "canceled"}

# Stays.net OTA flag -> channel name
OTA_FLAGS = {
    "_b_airbnb": "Airbnb",
    "_b_bookingcom": "Booking.com",
    "_b_expedia": "Expedia",
    "_b_homeaway": "HomeAway",
    "_b_temporadalivre": "TemporadaLivre",
    "_b_decolar": "Decolar",
    "_b_hvmi": "HVMI",
    "_b_flipkey": "FlipKey",
    "_b_googlevr": "Google VR",
}

# Stay status values (sensor.status)
STATUS_VACANT = "vacant"
STATUS_CHECKIN_TODAY = "checkin_today"
STATUS_OCCUPIED = "occupied"
STATUS_CHECKOUT_TODAY = "checkout_today"
