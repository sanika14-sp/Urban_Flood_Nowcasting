"""
app.py - Flask REST API for Urban Flood Nowcasting System.
Serves flood forecasts, hotspots, safe routing, drain blockage simulation, and rainfall feeds.
"""
import os
import json
import pandas as pd
from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS

from model.flood_model import FloodModel
from model.routing import FloodRouter
from model.drainage import DrainageNetwork

app = Flask(__name__, static_folder="static")
CORS(app)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
OUTPUTS_DIR = os.path.join(BASE_DIR, "outputs")
RAINFALL_CSV = os.path.join(DATA_DIR, "rainfall_event.csv")

# Initialize models
flood_model = FloodModel(
    dem_path=os.path.join(DATA_DIR, "dem.tif"),
    drains_path=os.path.join(DATA_DIR, "drains.geojson"),
    roads_path=os.path.join(DATA_DIR, "roads.geojson"),
    outputs_dir=OUTPUTS_DIR
)
router = FloodRouter(
    roads_geojson_path=os.path.join(DATA_DIR, "roads.geojson"),
    graphml_path=os.path.join(DATA_DIR, "roads.graphml")
)


def get_nearest_time_step(t_minutes: int) -> int:
    """Clamps and rounds t to nearest available 15-minute increment between 0 and 180."""
    try:
        t_int = int(t_minutes)
    except (ValueError, TypeError):
        t_int = 0
    t_clamped = max(0, min(180, t_int))
    return int(round(t_clamped / 15.0) * 15)


@app.route("/api/forecast", methods=["GET"])
def get_forecast():
    """
    GET /api/forecast?t=<minutes>
    Returns predicted street depths GeoJSON FeatureCollection at given lead time (0 - 180 min).
    """
    t_query = request.args.get("t", default=0, type=int)
    t = get_nearest_time_step(t_query)
    filename = f"depths_t{t:03d}.geojson"
    file_path = os.path.join(OUTPUTS_DIR, filename)

    # If outputs don't exist yet, run model once
    if not os.path.exists(file_path):
        if os.path.exists(RAINFALL_CSV):
            flood_model.run_simulation(RAINFALL_CSV)
        else:
            return jsonify({"error": "Rainfall data not found to generate forecast"}), 404

    if not os.path.exists(file_path):
        return jsonify({"error": f"Forecast for t={t} not found"}), 404

    with open(file_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    return jsonify(data)


@app.route("/api/hotspots", methods=["GET"])
def get_hotspots():
    """
    GET /api/hotspots?t=<minutes>&min_depth=15
    Returns surcharging nodes and streets above given depth threshold, ranked by depth descending.
    """
    t_query = request.args.get("t", default=60, type=int)
    min_depth = request.args.get("min_depth", default=15.0, type=float)
    t = get_nearest_time_step(t_query)

    filename = f"depths_t{t:03d}.geojson"
    file_path = os.path.join(OUTPUTS_DIR, filename)

    if not os.path.exists(file_path):
        if os.path.exists(RAINFALL_CSV):
            flood_model.run_simulation(RAINFALL_CSV)
        else:
            return jsonify({"error": "No forecast files available"}), 404

    with open(file_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    hotspots = []
    for feat in data.get("features", []):
        props = feat.get("properties", {})
        depth = float(props.get("depth_cm", 0.0))
        geom = feat.get("geometry", {})

        if depth >= min_depth:
            # Determine representative coordinate [lat, lng]
            coords = geom.get("coordinates", [])
            lat, lng = 0.0, 0.0
            if geom.get("type") == "Point" and len(coords) >= 2:
                lng, lat = coords[0], coords[1]
            elif geom.get("type") == "LineString" and len(coords) > 0:
                mid = coords[len(coords) // 2]
                lng, lat = mid[0], mid[1]

            hotspots.append({
                "id": feat.get("id") or props.get("id"),
                "name": props.get("name", "Unknown Hotspot"),
                "type": "hotspot_point" if props.get("is_hotspot") else "street_segment",
                "depth_cm": depth,
                "level": props.get("level", "Red" if depth > 30 else "Orange"),
                "status": props.get("status", "Flooded"),
                "location": [lat, lng],
                "time_min": t,
                "elevation_m": props.get("elevation_m"),
                "surcharged": props.get("surcharged", True)
            })

    # Sort descending by water depth
    hotspots.sort(key=lambda x: x["depth_cm"], reverse=True)

    return jsonify({
        "time_min": t,
        "min_depth_filter_cm": min_depth,
        "count": len(hotspots),
        "hotspots": hotspots
    })


@app.route("/api/route", methods=["POST"])
def post_route():
    """
    POST /api/route
    Body: { "from": [lat, lng], "to": [lat, lng], "t": 60, "mode": "emergency" }
    Calculates flood-safe route avoiding impassable streets.
    """
    body = request.get_json(force=True, silent=True) or {}
    from_pt = body.get("from")
    to_pt = body.get("to")
    t = body.get("t", 60)
    mode = body.get("mode", "emergency")

    if not from_pt or not to_pt:
        return jsonify({
            "error": "Missing 'from' or 'to' coordinates. Expected format: [lat, lng]"
        }), 400

    if len(from_pt) != 2 or len(to_pt) != 2:
        return jsonify({
            "error": "Coordinates must be 2-element arrays: [latitude, longitude]"
        }), 400

    t_clamped = get_nearest_time_step(t)
    route_result = router.calculate_safe_route(from_pt, to_pt, time_min=t_clamped, mode=mode)

    return jsonify(route_result)


@app.route("/api/drain/block", methods=["POST"])
def post_block_drain():
    """
    POST /api/drain/block
    Body: { "drain_id": "INLET_SION_CIRCLE", "blocked": true, "capacity_reduction": 1.0 }
    Marks a drain pipe or inlet as blocked and re-runs the coupled flood model.
    """
    body = request.get_json(force=True, silent=True) or {}
    drain_id = body.get("drain_id")
    blocked = bool(body.get("blocked", True))
    reduction = float(body.get("capacity_reduction", 1.0))

    if not drain_id:
        return jsonify({"error": "Missing 'drain_id'"}), 400

    # Apply blockage to drainage model
    flood_model.drainage.set_blockage(drain_id, blocked=blocked, reduction=reduction)

    # Re-run simulation to update forecast files in outputs/
    generated_files = flood_model.run_simulation(RAINFALL_CSV)

    return jsonify({
        "status": "success",
        "drain_id": drain_id,
        "blocked": blocked,
        "capacity_reduction": reduction,
        "active_blockages": flood_model.drainage.blocked_items,
        "updated_forecast_files_count": len(generated_files),
        "message": f"Drain '{drain_id}' blockage updated. Forecast recomputed."
    })


@app.route("/api/rainfall", methods=["GET"])
def get_rainfall():
    """
    GET /api/rainfall
    Returns rainfall time series profile.
    """
    if not os.path.exists(RAINFALL_CSV):
        return jsonify({"error": "Rainfall file not found"}), 404

    df = pd.read_csv(RAINFALL_CSV)
    records = df.to_dict(orient="records")
    return jsonify({
        "event_name": "Sion Pilot Monsoonal Storm",
        "duration_minutes": int(df["time_min"].max()),
        "peak_intensity_mm_hr": float(df["intensity_mm_per_hr"].max()),
        "time_series": records
    })


@app.route("/api/drains", methods=["GET"])
def get_drains():
    """
    GET /api/drains
    Returns GeoJSON of drain network (nodes and pipes).
    """
    drains_path = os.path.join(DATA_DIR, "drains.geojson")
    if not os.path.exists(drains_path):
        return jsonify({"error": "Drains GeoJSON not found"}), 404

    with open(drains_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    # Attach current blockage info
    for feat in data.get("features", []):
        f_id = feat.get("id")
        props = feat.get("properties", {})
        props["is_blocked"] = flood_model.drainage.is_blocked(f_id)
        props["blockage_reduction"] = flood_model.drainage.blocked_items.get(f_id, 0.0)

    return jsonify(data)


@app.route("/api/roads", methods=["GET"])
def get_roads():
    """
    GET /api/roads
    Returns base road network GeoJSON.
    """
    roads_path = os.path.join(DATA_DIR, "roads.geojson")
    if not os.path.exists(roads_path):
        return jsonify({"error": "Roads GeoJSON not found"}), 404

    with open(roads_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    return jsonify(data)


@app.route("/api/summary", methods=["GET"])
def get_summary():
    """
    GET /api/summary?t=<minutes>
    Returns high-level statistics for city officials and responders.
    """
    t_query = request.args.get("t", default=60, type=int)
    t = get_nearest_time_step(t_query)
    filename = f"depths_t{t:03d}.geojson"
    file_path = os.path.join(OUTPUTS_DIR, filename)

    if not os.path.exists(file_path):
        flood_model.run_simulation(RAINFALL_CSV)

    with open(file_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    features = data.get("features", [])
    road_feats = [f for f in features if not f.get("properties", {}).get("is_hotspot")]

    depths = [float(f["properties"].get("depth_cm", 0.0)) for f in road_feats]
    max_d = max(depths) if depths else 0.0
    avg_d = sum(depths) / len(depths) if depths else 0.0

    green = sum(1 for d in depths if d < 5.0)
    yellow = sum(1 for d in depths if 5.0 <= d <= 15.0)
    orange = sum(1 for d in depths if 15.0 < d <= 30.0)
    red = sum(1 for d in depths if d > 30.0)

    return jsonify({
        "pilot_area": "Sion, Mumbai",
        "lead_time_min": t,
        "available_time_steps": list(range(0, 195, 15)),
        "total_roads_monitored": len(road_feats),
        "max_depth_cm": round(max_d, 1),
        "average_depth_cm": round(avg_d, 1),
        "breakdown": {
            "passable_green": green,
            "slow_traffic_yellow": yellow,
            "risk_orange": orange,
            "impassable_red": red
        },
        "active_drain_blockages": list(flood_model.drainage.blocked_items.keys())
    })


@app.route("/", methods=["GET"])
def index():
    """Serves index.html if frontend is present, or returns API information."""
    index_file = os.path.join(app.static_folder, "index.html") if app.static_folder else None
    if index_file and os.path.exists(index_file):
        return send_from_directory(app.static_folder, "index.html")
    return jsonify({
        "name": "Urban Flood Nowcasting System API",
        "status": "online",
        "pilot_area": "Sion, Mumbai",
        "endpoints": [
            "GET /api/forecast?t=<minutes>",
            "GET /api/hotspots?t=<minutes>&min_depth=15",
            "POST /api/route",
            "POST /api/drain/block",
            "GET /api/rainfall",
            "GET /api/drains",
            "GET /api/roads",
            "GET /api/summary?t=<minutes>"
        ]
    })


if __name__ == "__main__":
    # Ensure forecast data exists on startup
    if not os.path.exists(os.path.join(OUTPUTS_DIR, "depths_t000.geojson")):
        flood_model.run_simulation(RAINFALL_CSV)

    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)
