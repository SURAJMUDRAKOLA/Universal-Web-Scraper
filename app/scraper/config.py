"""Central resource limits — all magic numbers live here, nowhere else."""

# ── HTTP fetch ────────────────────────────────────────────────────────────────
FETCH_CONNECT_TIMEOUT = 10        # seconds
FETCH_READ_TIMEOUT    = 20        # seconds
FETCH_TOTAL_TIMEOUT   = 30        # seconds
MAX_RESPONSE_BYTES    = 10_000_000  # 10 MB

# ── Browser ───────────────────────────────────────────────────────────────────
BROWSER_TIMEOUT          = 30_000  # ms — page.goto() and evaluate()
BROWSER_WAIT_AFTER_LOAD  = 1_500   # ms — fixed wait after domcontentloaded
BROWSER_WAIT_AFTER_NAV   = 1_200   # ms — wait after each pagination click

# ── Interaction limits ────────────────────────────────────────────────────────
MAX_SCROLLS              = 4
MAX_PAGES                = 5       # absolute ceiling
MIN_PAGES                = 3       # assignment depth requirement
MAX_CLICKS               = 10
MAX_INTERACTION_RUNTIME  = 60      # seconds total for all interaction steps

# ── Extraction ────────────────────────────────────────────────────────────────
RAW_HTML_TRUNCATE        = 2_000   # chars per section
MAX_TEXT_PER_SECTION     = 6_000   # chars
MAX_TOTAL_TEXT           = 80_000  # chars across all sections
MAX_SECTIONS             = 60
MAX_LINKS                = 100     # per section
MAX_IMAGES               = 40      # per section
MAX_LISTS                = 20
MAX_TABLES               = 20

# ── Content quality thresholds ────────────────────────────────────────────────
MIN_TEXT_LENGTH_STATIC   = 500     # chars — below this, consider JS fallback
MIN_PARAGRAPH_COUNT      = 2
MIN_TEXT_DENSITY         = 0.05
