import json
import os

CONFIG_PATH = os.path.join(os.path.dirname(__file__), "config.json")

REQUIRED_KEYS = ["geo_latitude", "geo_longitude", "geo_radius_km"]


def load_config() -> dict:
    with open(CONFIG_PATH) as f:
        cfg = json.load(f)
    for key in REQUIRED_KEYS:
        if key not in cfg:
            raise ValueError(f"Missing required config key: {key}")
    print(f"[CONFIG] geo={cfg['geo_latitude']},{cfg['geo_longitude']} "
          f"radius={cfg['geo_radius_km']}km")
    return cfg
