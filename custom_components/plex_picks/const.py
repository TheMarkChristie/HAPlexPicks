"""Constants for Plex Picks. Nothing here imports Home Assistant, so the model and its tests stay pure."""

DOMAIN = "plex_picks"
NAME = "Plex Picks"

# the card, served by the integration from its www folder and registered as a dashboard resource
URL_BASE = "/plex_picks"
CARD_FILE = "plex-picks-card.js"

# the image proxy: /api/plex_picks/img/<kind>/<rating_key>/<WxH>, signed per request so the Plex token never leaves the server
IMAGE_URL = "/api/plex_picks/img"
IMAGE_KINDS = ("thumb", "art")
SIGNED_URL_HOURS = 12
# the only sizes the websocket signs, and so the only ones the proxy serves
POSTER = "300x450"
ROW_ART = "600x338"
BACKDROP = "1280x720"
IMAGE_SIZES = (POSTER, ROW_ART, BACKDROP)

# config entry data
CONF_URL = "url"
CONF_TOKEN = "token"
CONF_SERVER_NAME = "server_name"
CONF_SERVER_ID = "server_id"

# options
CONF_PLAYERS = "players"                    # media_player entities offered on the Play button; empty: the house's Cast devices
CONF_PLEX_PLAYER_FOR = "plex_player_for"    # "media_player.tv=media_player.plex_tv" lines: the Plex client that appears when that TV opens Plex
CONF_SHORTLIST_URL = "shortlist_url"        # optional: Shortlist's base URL, for run status
CONF_SHORTLIST_TOKEN = "shortlist_token"    # optional: Shortlist's owner API token
CONF_INTERVAL = "interval_minutes"
CONF_ROW_SIZE = "row_size"                  # most items read per row
CONF_CONTINUE = "continue_watching"         # include the owner's Continue Watching row
CONF_RECENT = "recently_added"              # include a New on the server row
CONF_RECENT_SIZE = "recent_size"

DEFAULT_INTERVAL = 30
DEFAULT_ROW_SIZE = 40
DEFAULT_RECENT_SIZE = 20
MIN_INTERVAL = 5
MAX_INTERVAL = 720

# events and websocket
EVENT_UPDATED = f"{DOMAIN}_updated"            # fired when the rows changed, with their version
WS_DATA = f"{DOMAIN}/data"
WS_SUBSCRIBE = f"{DOMAIN}/subscribe"           # any signed-in user (subscribe_events refuses custom events to non-admins)
WS_SHORTLIST = f"{DOMAIN}/shortlist"           # admins only: relay one /api/ request to Shortlist with the configured token

# how hard Plex is asked: one request at a time a third of a second apart, short timeouts, the whole read bounded,
# and each library's Shortlist label looked up once a day (Shortlist writes at most nightly)
PLEX_GAP_SECONDS = 0.34
PLEX_TIMEOUT_SECONDS = 15
IMAGE_TIMEOUT_SECONDS = 10
READ_BUDGET_SECONDS = 120
LABEL_CACHE_HOURS = 24

# services
SERVICE_REFRESH = "refresh"
SERVICE_PLAY = "play"
ATTR_RATING_KEY = "rating_key"
ATTR_PLAYER = "player"
ATTR_MEDIA_TYPE = "media_type"
ATTR_RESUME = "resume"

# how long the play service waits for a TV to open Plex and show up as a Plex client
PLAY_WAIT_SECONDS = 30

# Shortlist's labels on its collections
LABEL_PLAIN = "shortlist"
LABEL_PREFIX = "shortlist_"

PLEX_SCHEME = "plex://"
