# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions follow
[Semantic Versioning](https://semver.org/).

## [1.1.0] - 2026-09-03

### Fixed

- `get_historical` asked the archive for the wrong range in December: the
  month end was computed as the *next* year's November 30, so a December
  request stretched to today, poisoned the cache and often failed with
  `502 Bad Gateway`. Month ends now come from `calendar.monthrange`.
- Months merged in non-chronological order (freshly fetched first, cached
  after), so the `time` axis was not monotonic.
- Merging months with different variable sets produced series of different
  lengths; `to_dataframe` then failed with
  `ValueError: All arrays must be of the same length`. Every requested
  variable is now aligned to `len(time)` and an `OpenMeteoDataError` is
  raised instead of returning inconsistent data.
- A month fetched before it was over was cached as complete and stayed
  truncated forever. Cache entries now carry coverage metadata and a
  `final` flag; non-final months are re-fetched after `recent_ttl`.
- Historical cache keys ignored `timezone`; forecast cache keys ignored
  `days` and `timezone`, so a 3-day forecast could be served for a 7-day
  request.
- HTTP 4xx responses carrying `{"error": true, "reason": ...}` were reported
  as `OpenMeteoConnectionError`; they are now `OpenMeteoAPIError(reason)`.
- Historical-data validation compared against the local date instead of UTC.

### Added

- Pluggable cache backends in `openmeteo.cache`: `MemoryBackend`,
  `FileBackend` (atomic writes, filesystem I/O off the event loop) and
  `RedisBackend` (standalone or cluster, TLS with custom CA), plus
  `backend_from_url()`. Redis needs the optional extra:
  `pip install "openmeteo-py-df[redis]"`.
- Client configuration: `cache`, `cache_url`, the `OPENMETEO_CACHE_URL`
  environment variable (`memory://`, `file:///path`, `redis://...`,
  `rediss://...?cluster=true&ca_bundle=/path`), `recent_ttl`,
  `historical_ttl`, `cache_strict`, `retries`, `retry_backoff`,
  `max_concurrency`.
- Read-only filesystems: when the default cache directory is not writable
  the client falls back to an in-memory cache and logs one warning instead
  of failing.
- Retries with exponential backoff and jitter for transport errors, HTTP 429
  and 5xx; `Retry-After` is honoured.
- Missing months are fetched concurrently (bounded by `max_concurrency`).
- `client.cache` property and `await client.cache_health()` for dependency
  health checks.
- `OpenMeteoDataError`, `__version__`, `ARCHIVE_LAG_DAYS` and related
  constants.
- Requested variables are always present in responses as lists of
  `len(time)` values, also for forecasts; unrequested variables stay `None`.

### Changed

- `to_dataframe()` no longer emits columns for variables that were not
  requested. Pass `include_empty=True` for the previous behaviour.
- `clear_forecast_cache()`, `clear_historical_cache()` and
  `clear_all_cache()` are coroutines and return the number of removed
  entries.
- Cache entry format is versioned (`openmeteo:v2:` key prefix). Files
  written by 1.0.x are ignored and the data is fetched again once.
- `HISTORY_RECENT_DAYS` is kept for import compatibility but no longer used;
  freshness is governed by the `final` flag, `recent_ttl` and
  `historical_ttl`.

### Removed

- `HistoricalCache` and `ForecastCache` (replaced by backends and stores).
  The `cache_dir` constructor argument still works as an alias for
  `cache_url="file://<dir>"`.

## [1.0.1] - 2026-05-19

- Export `CURRENT_VARIABLES`, `HOURLY_VARIABLES`, `DAILY_VARIABLES` from the
  package root; PyPI metadata fixes.

## [1.0.0]

- Initial public release.
