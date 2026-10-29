# Genuine local workflow recording

`demo.mp4` is a capture of the actual SignalDesk Live browser application against
its PostgreSQL-backed API and worker. Long idle waits are shortened to three
seconds per captured state; no application values or UI states were fabricated.
Raw screencast frame timestamps are retained in `artifacts/live/evidence/recording/frames.json`.

The source edit commits revision 3 while the worker is stopped. The app withholds
current predictions, then shows revision 2 only after explicit stale opt-in.
A worker pauses after a durable revision-3 claim and is killed with SIGKILL.
The replacement worker recovers; the app reads both fresh revision-3 features,
with return probability 64.1% and expected revenue 420 for synthetic C0000.
The information cutoff remains July 15, 2024. These are not predictive-quality
validation results.

Supporting receipts: `demo-midclaim-status.json`, `demo-recovered-status.json`,
`demo-recovered-return.json`, `demo-recovered-revenue.json` and the fault-injector
source are under `artifacts/live/evidence`. The process fault uses real process
termination; the recording itself shows the application consequences.
