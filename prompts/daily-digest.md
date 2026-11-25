The daily digest is no longer written by a model. It is produced by `scripts/jobs_digest.py`, run as a
no-agent cron job so that its stdout is delivered as is (empty output = nothing is sent):

    hermes cron edit <digest job id> --no-agent --script jobs_digest.py

It lists responses and new alerts (fit 40+, never links the user sent themselves, never applied cards) since
the previous digest, then counts taken from the board, and refreshes jobs-board.html / applications.csv.
Preview without recording: `python3 ~/.hermes/scripts/jobs_digest.py --dry-run`.
