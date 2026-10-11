"""
app.py - Flask REST API for Urban Flood Nowcasting System.
Serves flood forecasts, hotspots, safe routing, drain blockage simulation, and rainfall feeds.
"""
import os
import json
import numpy as np
import pandas as pd
from flask import Flask, request, jsonify, send_from_directory, render_template
from flask_cors import CORS

from model.flood_model import FloodModel
from model.routing import FloodRouter
from model.drainage import DrainageNetwork

app = Flask(__name__, static_folder="static", template_folder="templates")
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


@app.route("/api/route/compare", methods=["POST"])
def post_route_compare():
    """
    POST /api/route/compare
    Body: { "from": [lat, lng], "to": [lat, lng], "t": 60, "mode": "emergency" }
    Returns side-by-side comparison of baseline dry-weather route vs flood-safe route.
    """
    body = request.get_json(force=True, silent=True) or {}
    from_pt = body.get("from")
    to_pt = body.get("to")
    t = body.get("t", 60)
    mode = body.get("mode", "emergency")

    if not from_pt or not to_pt or len(from_pt) != 2 or len(to_pt) != 2:
        return jsonify({"error": "Missing valid 'from' and 'to' [lat, lng] coordinates"}), 400

    t_clamped = get_nearest_time_step(t)
    comparison = router.calculate_route_comparison(from_pt, to_pt, time_min=t_clamped, mode=mode)
    return jsonify(comparison)


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


@app.route("/api/drain/reset", methods=["POST"])
def post_reset_drains():
    """
    POST /api/drain/reset
    Clears all active drain blockages and re-runs the baseline flood model.
    """
    flood_model.drainage.reset_blockages()
    generated_files = flood_model.run_simulation(RAINFALL_CSV)
    return jsonify({
        "status": "success",
        "message": "All drain blockages cleared. Baseline forecasts restored.",
        "active_blockages": {},
        "updated_forecast_files_count": len(generated_files)
    })


@app.route("/api/drain/status", methods=["GET"])
def get_drain_status():
    """
    GET /api/drain/status
    Returns full operational list of drain nodes and pipes with blockage details.
    """
    all_nodes = flood_model.drainage.get_all_nodes()
    all_pipes = flood_model.drainage.get_all_pipes()

    nodes_list = []
    for nid, data in all_nodes.items():
        blocked = flood_model.drainage.is_blocked(nid)
        nodes_list.append({
            "id": nid,
            "name": data.get("name", nid),
            "type": data.get("type", "inlet"),
            "ponding_area_m2": data.get("ponding_area_m2", 1500),
            "coords": data.get("coords", []),
            "is_blocked": blocked,
            "reduction": flood_model.drainage.blocked_items.get(nid, 0.0)
        })

    pipes_list = []
    for pid, data in all_pipes.items():
        blocked = flood_model.drainage.is_blocked(pid)
        pipes_list.append({
            "id": pid,
            "from_node": data.get("from_node"),
            "to_node": data.get("to_node"),
            "base_capacity": data.get("base_capacity", 45.0),
            "diameter_m": data.get("diameter_m", 1.2),
            "is_blocked": blocked,
            "reduction": flood_model.drainage.blocked_items.get(pid, 0.0)
        })

    return jsonify({
        "total_nodes": len(nodes_list),
        "total_pipes": len(pipes_list),
        "active_blocked_count": len(flood_model.drainage.blocked_items),
        "nodes": nodes_list,
        "pipes": pipes_list
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


@app.route("/api/alerts", methods=["GET"])
def get_alerts():
    """
    GET /api/alerts?t=<minutes>
    Returns real-time citizen & emergency alerts for severely waterlogged roads and low spots.
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
    critical_inundations = []
    moderate_inundations = []

    for feat in features:
        props = feat.get("properties", {})
        depth = float(props.get("depth_cm", 0.0))
        name = props.get("name", "Unknown Road")
        if depth >= 30.0:
            critical_inundations.append({"name": name, "depth_cm": round(depth, 1), "level": "RED"})
        elif depth >= 15.0:
            moderate_inundations.append({"name": name, "depth_cm": round(depth, 1), "level": "ORANGE"})

    if critical_inundations:
        alert_level = "RED ALERT"
        color = "#ef4444"
        message = f"Severe flooding at {len(critical_inundations)} locations including {critical_inundations[0]['name']} ({critical_inundations[0]['depth_cm']}cm). Evacuate/divert traffic."
    elif moderate_inundations:
        alert_level = "ORANGE WARNING"
        color = "#f97316"
        message = f"Waterlogging detected at {len(moderate_inundations)} locations. Small vehicles and two-wheelers advise caution."
    else:
        alert_level = "GREEN CLEAR"
        color = "#10b981"
        message = "Road network currently passable. All monitored sectors clear."

    return jsonify({
        "time_min": t,
        "overall_level": alert_level,
        "color": color,
        "message": message,
        "critical_count": len(critical_inundations),
        "moderate_count": len(moderate_inundations),
        "critical_roads": critical_inundations,
        "moderate_roads": moderate_inundations,
        "active_blockages_count": len(flood_model.drainage.blocked_items),
        "timestamp_issued": f"Nowcast +{t}m"
    })


@app.route("/api/simulate", methods=["POST"])
def post_simulate():
    """
    POST /api/simulate
    Body: { "storm_type": "cloudburst"|"heavy_monsoon"|"moderate"|"custom", "peak_intensity": 100.0, "duration_min": 180 }
    Simulates custom storm scenario and recomputes all nowcast forecast GeoJSONs.
    """
    body = request.get_json(force=True, silent=True) or {}
    storm_type = body.get("storm_type", "heavy_monsoon")
    peak = float(body.get("peak_intensity", 75.0))
    duration = int(body.get("duration_min", 180))

    times = list(range(0, duration + 15, 15))
    t_peak = duration * 0.35
    records = []
    for t in times:
        if storm_type == "cloudburst":
            sigma = 20.0
            intensity = peak * np.exp(-((t - 45) ** 2) / (2 * (sigma ** 2)))
        elif storm_type == "moderate":
            sigma = 45.0
            intensity = (peak * 0.6) * np.exp(-((t - 60) ** 2) / (2 * (sigma ** 2)))
        else:
            sigma = 35.0
            intensity = peak * np.exp(-((t - t_peak) ** 2) / (2 * (sigma ** 2)))
        records.append({"time_min": t, "intensity_mm_per_hr": round(float(max(0.0, intensity)), 1)})

    df_scenario = pd.DataFrame(records)
    generated_files = flood_model.run_simulation(df_scenario)

    return jsonify({
        "status": "success",
        "storm_type": storm_type,
        "peak_intensity_mm_hr": peak,
        "duration_minutes": duration,
        "timesteps_computed": len(generated_files),
        "rainfall_schedule": records,
        "message": f"Simulation complete for {storm_type} (peak {peak} mm/h). {len(generated_files)} timesteps updated."
    })


@app.route("/api/status", methods=["GET"])
def get_system_status():
    """GET /api/status - Returns system diagnostics, active models, and pilot boundary."""
    return jsonify({
        "system": "Urban Flood Nowcasting Engine",
        "version": "1.0.0",
        "status": "online",
        "pilot_area": {
            "name": "Sion, Mumbai",
            "bounds": {"west": 72.850, "south": 19.030, "east": 72.875, "north": 19.055},
            "resolution_m": 30
        },
        "active_blockages": list(flood_model.drainage.blocked_items.keys()),
        "available_lead_times_min": list(range(0, 195, 15))
    })


@app.route("/api/stats/drainage", methods=["GET"])
def get_drainage_stats():
    """GET /api/stats/drainage - Drainage network performance and capacity stats."""
    drains_path = os.path.join(DATA_DIR, "drains.geojson")
    with open(drains_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    nodes = [f for f in data.get("features", []) if f.get("properties", {}).get("feature_class") == "drain_node"]
    pipes = [f for f in data.get("features", []) if f.get("properties", {}).get("feature_class") == "drain_pipe"]

    blocked_nodes = [n["id"] for n in nodes if flood_model.drainage.is_blocked(n["id"])]
    blocked_pipes = [p["id"] for p in pipes if flood_model.drainage.is_blocked(p["id"])]

    return jsonify({
        "total_nodes": len(nodes),
        "total_pipes": len(pipes),
        "blocked_nodes": blocked_nodes,
        "blocked_pipes": blocked_pipes,
        "blockage_count": len(flood_model.drainage.blocked_items),
        "outfalls": ["OUTFALL_MAHIM_1", "OUTFALL_MAHIM_2", "OUTFALL_MAHIM_3"],
        "pilot_capacity_nominal_mm_hr": 45.0
    })


# -------------------------------------------------------------
# Frontend Page Routes (Templates)
# -------------------------------------------------------------
@app.route("/", methods=["GET"])
def index():
    """Serves the main interactive GIS Map Dashboard."""
    return render_template("index.html")


@app.route("/tester", methods=["GET"])
def tester_page():
    """Serves the Municipal Engineer & Simulation Control Room."""
    return render_template("tester.html")


@app.route("/routing", methods=["GET"])
def routing_page():
    """Serves the Flood-Safe Route Comparison & Evacuation page."""
    return render_template("routing.html")


@app.route("/api-tester", methods=["GET"])
@app.route("/api-test", methods=["GET"])
def api_tester_page():
    """Serves the REST API Test Console."""
    return render_template("api_tester.html")


@app.route("/api", methods=["GET"])
def api_directory():
    """Returns directory of all REST API endpoints."""
    return jsonify({
        "name": "Urban Flood Nowcasting System API",
        "status": "online",
        "pilot_area": "Sion, Mumbai",
        "endpoints": [
            "GET /api/summary?t=<minutes>",
            "GET /api/forecast?t=<minutes>",
            "GET /api/hotspots?t=<minutes>&min_depth=15",
            "POST /api/route",
            "POST /api/route/compare",
            "POST /api/drain/block",
            "POST /api/drain/reset",
            "GET /api/drain/status",
            "GET /api/rainfall",
            "POST /api/simulate",
            "GET /api/alerts?t=<minutes>",
            "GET /api/stats/drainage",
            "GET /api/drains",
            "GET /api/roads",
            "GET /api/status"
        ]
    })


if __name__ == "__main__":
    # Ensure forecast data exists on startup
    if not os.path.exists(os.path.join(OUTPUTS_DIR, "depths_t000.geojson")):
        flood_model.run_simulation(RAINFALL_CSV)

    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)
