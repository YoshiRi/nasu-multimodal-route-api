"""Query GTFS feeds from multiple transit operators as one network.

See README.md for the problems this solves: ID collisions across feeds,
unboardable parent stations, the same physical stop registered several
times, and journeys that need a change of vehicle at a shared or
walkable-nearby stop.
"""

from gtfs_multifeed.engine import (
    GTFSEngine,
    DEFAULT_TRANSFER_RADIUS_M,
    DirectBusOption,
    OPTIONAL_GTFS_FILES,
    REQUIRED_GTFS_FILES,
    discover_gtfs_feeds,
    haversine_distance_m,
    parse_gtfs_time_to_minutes,
    parse_service_date,
    parse_service_date_to_gtfs_date,
)
from gtfs_multifeed.feeds import (
    FeedConfig,
    download_zip,
    extract_gtfs_zip,
    load_manifest,
    print_status,
    read_feed_info,
    run_cli,
    save_manifest,
    sha256_file,
    update_feed,
)

__all__ = [
    "GTFSEngine",
    "DEFAULT_TRANSFER_RADIUS_M",
    "DirectBusOption",
    "FeedConfig",
    "OPTIONAL_GTFS_FILES",
    "REQUIRED_GTFS_FILES",
    "discover_gtfs_feeds",
    "download_zip",
    "extract_gtfs_zip",
    "haversine_distance_m",
    "load_manifest",
    "parse_gtfs_time_to_minutes",
    "parse_service_date",
    "parse_service_date_to_gtfs_date",
    "print_status",
    "read_feed_info",
    "run_cli",
    "save_manifest",
    "sha256_file",
    "update_feed",
]
