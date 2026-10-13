# gtfs-multifeed

Query GTFS feeds from **multiple transit operators as a single network**.

Regional transit is usually split across several operators, each publishing
its own GTFS feed. Merging them naively breaks in ways that are easy to miss
and annoying to debug. This library handles those cases.

> **Scope: up to one transfer.** `search_journeys()` will change vehicles
> once, at a shared stop or one a short walk away. Journeys needing two or
> more changes are out of reach — for those, use a full routing engine like
> OpenTripPlanner.

```bash
pip install gtfs-multifeed
```

## What it solves

**ID collisions across feeds.** Independent operators reuse the same short
IDs — `stop_id` `1_01` may exist in three unrelated feeds. `from_feeds()`
namespaces every ID with its feed name (`nasushiobara:1_01`) so a merged
network stays unambiguous.

```python
from gtfs_multifeed import GTFSEngine

engine = GTFSEngine.from_feeds({
    "operator_a": "data/gtfs/operator_a",
    "operator_b": "data/gtfs/operator_b",
})
```

**Unboardable parent stations.** GTFS `location_type=1` entries are station
groupings, not places a vehicle stops. In one real feed these were 33% of all
stops, and a naive nearest-stop search kept returning them. `nearby_stops()`
only returns stops that actually appear in `stop_times`.

**The same physical stop registered many times.** Operators commonly register
separate entries per direction, metres apart. A "find the nearest stop"
search returns near-duplicates that are all equivalent, so if you only try
the top few candidates you can miss the one stop that's actually connected to
your destination. `nearby_stops()` returns every candidate ranked by
distance, so callers can widen their search rather than guess.

**Shape geometry for a single ride.** `bus_leg_path()` returns the route
shape sliced to just the segment between boarding and alighting — for drawing
the ride on a map, rather than the operator's whole route.

**Journeys that need a change of vehicle.** Most GTFS feeds ship no
`transfers.txt`, so there is nothing saying which stops connect.
`transfer_edges()` infers them from geography — any two boardable stops
within walking distance — and `search_journeys()` searches over that.
On one regional network this took reachability between tourist spots from
18% of pairs to 82%, including changes *between operators*, which is only
possible because the feeds were merged in the first place.

## Usage

```python
from gtfs_multifeed import GTFSEngine

engine = GTFSEngine("data/gtfs/my_feed")   # or .from_feeds({...})

# One vehicle all the way through.
engine.search_next_bus(
    origin_stop_id="stop_a",
    dest_stop_id="stop_b",
    departure_time="10:00",        # "HH:MM" or GTFS "HH:MM:SS" (hours ≥24 OK)
    departure_date="2026-08-17",   # optional; filters by calendar/calendar_dates
)

# Same, but allowed to change vehicles once. Returns the direct trips too,
# sorted by arrival time.
engine.search_journeys(
    origin_stop_id="stop_a",
    dest_stop_id="stop_b",
    departure_time="10:00",
    departure_date="2026-08-17",
    max_transfers=1,               # 0 restricts it to direct trips
    max_distance_m=300,            # how far a transfer may walk
)

engine.transfer_edges(max_distance_m=300)                    # the transfer graph
engine.nearby_stops(lat=37.095, lng=140.001, radius_m=1000)  # nearest first
engine.stop_info("stop_a")                                   # name + coordinates
engine.route_shapes()                                        # polyline per shape_id
engine.bus_leg_path(trip_id, origin_stop_id, dest_stop_id)   # ride segment only
engine.active_service_ids("2026-08-17")
engine.service_periods()             # {feed_name: (first_date, last_date)}
engine.feeds_not_covering("2027-05-01")  # feeds whose timetable misses that day
```

A journey comes back as `{"legs": [...], "transfers": [...],
"departure_time", "arrival_time", "duration_min", "num_transfers"}`, where
each leg has the same shape `search_next_bus()` returns and each transfer
names the stops, the distance, and the minutes to allow.

Transfer walking time is estimated from straight-line distance inflated by a
detour factor, and floored at a couple of minutes — a bus will not wait.
Building the graph is fast enough to not need caching strategy: 3,164 stops
(5M pairs) took 0.09s, and the result is cached per radius anyway.

Feeds from different operators expire on different dates, so a merged
engine can have buses from one operator on a given day and silently none
from another. An empty search result alone can't tell "no bus fits" from
"no timetable for that day"; `feeds_not_covering()` can, so callers can
tell their users which one happened.

`nearby_stops()` is vectorized over cached coordinate arrays — route
planners call it once per endpoint of every leg they consider, so it is
hot: 0.7ms against 3,164 stops.

Command line:

```bash
gtfs-multifeed search-bus --gtfs-dir data/gtfs/my_feed \
  --from stop_a --to stop_b --time 10:00 [--date 2026-08-17] [--json]
```

## Keeping feeds up to date

`FeedConfig` + `run_cli()` download feeds, extract them safely (archive
entries that would escape the destination directory are rejected before
anything is written), and record each feed's `feed_info.txt` validity period
and checksum in a JSON manifest — so you can tell when a feed was last
refreshed and whether it has expired.

Feed URLs live in your source; secrets stay in the environment.

```python
from pathlib import Path
from gtfs_multifeed import FeedConfig, run_cli

FEEDS = [
    FeedConfig(
        slug="operator_a",
        dest_dir=Path("data/gtfs/operator_a"),
        default_url="https://example.org/feeds/operator_a.zip",
        url_env_var="OPERATOR_A_GTFS_URL",       # optional override
    ),
    FeedConfig(
        slug="operator_b",
        dest_dir=Path("data/gtfs/operator_b"),
        default_url="https://example.org/api?key={api_key}",
        url_env_var="OPERATOR_B_GTFS_URL",
        api_key_env_var="OPERATOR_B_API_KEY",    # read from the environment
    ),
]

raise SystemExit(run_cli(FEEDS, Path("data/gtfs_manifest.json")))
```

Then `--status` reports each feed's freshness, and `--feed <slug>` updates
just one.

`discover_gtfs_feeds(data_root)` returns `{name: path}` for every valid feed
directory under a root — ready to hand to `from_feeds()`.

## Requirements

Python 3.11+, `pandas`, `httpx`. Feeds are read from extracted GTFS CSV
files (`stops.txt`, `routes.txt`, `trips.txt`, `stop_times.txt` required;
`calendar.txt`, `calendar_dates.txt`, `shapes.txt` used when present).

## License

MIT
