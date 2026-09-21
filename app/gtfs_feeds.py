"""The GTFS feeds this app loads: Nasushiobara City, Nasu Town, Kanto Jidousha.

Only the Kanto Jidousha API key is secret (read from the environment, see
.env.example); the endpoints and agency_id are public and live here. Any
feed's URL can be overridden via its <SLUG>_GTFS_URL environment variable,
e.g. to point at a local test server.
"""

from __future__ import annotations

from pathlib import Path

from gtfs_multifeed import FeedConfig

REPO_ROOT = Path(__file__).resolve().parents[1]
GTFS_DATA_ROOT = REPO_ROOT / "data" / "gtfs"
MANIFEST_PATH = REPO_ROOT / "data" / "gtfs_manifest.json"

FEEDS: list[FeedConfig] = [
    FeedConfig(
        slug="nasushiobara",
        dest_dir=GTFS_DATA_ROOT / "nasushiobara",
        default_url="https://api.gtfs-data.jp/v2/organizations/nasushiobaracity/feeds/chiikibasu/files/feed.zip",
        url_env_var="NASUSHIOBARA_GTFS_URL",
    ),
    FeedConfig(
        slug="nasutown",
        dest_dir=GTFS_DATA_ROOT / "nasutown",
        default_url="https://api.gtfs-data.jp/v2/organizations/nasutown/feeds/nasutown/files/feed.zip",
        url_env_var="NASUTOWN_GTFS_URL",
    ),
    FeedConfig(
        slug="kanto_auto",
        dest_dir=GTFS_DATA_ROOT / "kanto_auto",
        # agency_id 0904 = 関東自動車, found via POST https://www.ptd-hs.jp/GetAgencyList
        default_url="https://www.ptd-hs.jp/GetData?agency_id=0904&uid={api_key}",
        url_env_var="KANTO_BUS_GTFS_URL",
        api_key_env_var="KANTO_BUS_GTFS_API_KEY",
    ),
]
