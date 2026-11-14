"""Handler registry: job kind -> handler. A due kind missing here is skipped and logged (spec §9)."""

from worker import firms_poll
from worker.schedules import FIRMS_POLL

# monthly_reconcile gets its handler in Phase 2; until then its schedule row stays disabled.
HANDLERS = {FIRMS_POLL: firms_poll.run}
