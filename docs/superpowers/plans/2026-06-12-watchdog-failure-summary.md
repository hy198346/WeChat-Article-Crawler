# Watchdog Failure Summary Plan

## Objective

Implement a structured watchdog failure summary file for `WeChat-Article-Crawler` so scripts and external monitors can inspect current watchdog health without parsing logs.

## Scope

- Update `scripts/wechat_article_crawler/watchdog.py`
- Extend `tests/test_watchdog_schedule.py`
- Add design documentation in `docs/superpowers/specs/2026-06-12-watchdog-failure-summary-design.md`

## Tasks

### Task 1. Failure Summary Writer

- Add a helper that writes `output/watchdog_last_failure.json` when issues are detected
- Use atomic replace to avoid partial writes
- Keep the payload minimal and aligned with the design doc

### Task 2. Healthy Cleanup

- Remove the failure summary file when watchdog returns to a healthy state
- Ensure cleanup failure does not break the watchdog main flow

### Task 3. Verification And Delivery

- Run regression tests with `python3 -m pytest -q`
- Manually run `python3 scripts/wechat_article_crawler/watchdog.py`
- Check editor diagnostics for touched files
- Review `git status`
- Commit the code and documentation together
- Push the result to `origin/main`
- If launchd needs a refresh after deployment, run an appropriate `launchctl kickstart`

## Verification

- `python3 -m pytest -q`
- `python3 scripts/wechat_article_crawler/watchdog.py`
- Diagnostics for touched files are clean

## Notes

- Runtime artifacts stay under `output/` and `logs/`
- The failure summary is a current-state file, not a history archive
