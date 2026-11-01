# SignalDesk Live

A separate application imports only `aheaddb.live.client.Client` and public wire
contracts. It has no source database credentials or private engine cache. It uses
the v1 HTTP service for customer-return and revenue-planning features over shared
orders. The source is explicitly synthetic and historical.

From an installation with `aheaddb[live]`, start an owned PostgreSQL database and
set `AHEAD_LIVE_DSN` in the API/worker terminals (the application does not need it):

```sh
ahead live demo
ahead live worker
# second terminal
ahead live serve
# third terminal, no AHEAD_LIVE_DSN needed
python examples/live_desk/app.py
```

Open http://127.0.0.1:8791. Record an order. Stop the worker, record another, check
that current estimates are unavailable, then restart the worker and check the
source revision. Old results require the explicit checkbox. Separate reads are
not a mutually atomic bundle; the application reports differing revisions.

The app accepts only local origins, has bounded edit bodies and suppresses access
logs containing customer IDs. It is a local example, without production auth.
It sends no outreach, places no purchases and changes only the owned synthetic
source. See the [release quickstart](../../docs/live-quickstart.md) and
[verification record](../../docs/live-verification.md) for installed SDK and
actual worker-loss checks. Public release verification is a separate gate.

The browser expires its receipt after 60 seconds and hides values unless the
older-result checkbox is selected. Returning to the tab checks elapsed monotonic
time and clock discrepancies. Recording new source orders remains available for
known customers while prediction results are unavailable; prediction downtime
must not prevent recording the underlying event.
