# Betclic Bookmaker Intelligence V8

Public source-code mirror for the Betclic odds intelligence project.

## Data protection

Production SQLite data, private release assets, API credentials, and raw collection artifacts are intentionally excluded from this repository. Runtime persistence remains in the original private repository. The public collector must be configured with the `PRIVATE_REPO_TOKEN` and `FOOTBALL_DATA_API_TOKEN` Actions secrets before scheduled runs can operate.

## Readiness policy

The system does not claim model readiness until at least 200 unique Betclic matches have confirmed outcomes and usable pre-match odds. If evidence is insufficient, the expected status is `INSUFFICIENT_DATA` / `NO BET`.

## Setup

1. Add `PRIVATE_REPO_TOKEN` as an Actions secret. It must have access to the original private repository and its Releases.
2. Add `FOOTBALL_DATA_API_TOKEN` as an Actions secret if Football-Data result collection is required.
3. Review `.github/workflows/main.yml` before enabling the hourly schedule.
