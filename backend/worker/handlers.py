"""Handler registry: job kind -> handler. A due kind missing here is skipped and logged (spec §9)."""

from worker import firms_poll
from worker.schedules import FIRMS_POLL

# monthly_reconcile has no handler yet (out of scope of Phase 4); its schedule row stays disabled.
HANDLERS = {FIRMS_POLL: firms_poll.run}
