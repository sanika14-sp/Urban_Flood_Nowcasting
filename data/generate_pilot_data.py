"""
Generates pilot data for Sion, Mumbai:
1. dem.tif: 30m resolution digital elevation model with realistic micro-topography
2. drains.geojson: Storm drain network (inlets, pipes, outfalls to Mahim Creek)
3. roads.geojson & roads.graphml: Road network with major junctions
"""
import os
import json
import numpy as np
import rasterio
from rasterio.transform import from_bounds
import networkx as nx
from shapely.geometry import LineString, Point, mapping

# Spatial bounds for Sion Pilot Area
# Lon min/max: 72.850, 72.875
# Lat min/max: 19.030, 19.055
WEST, SOUTH, EAST, NORTH = 72.850, 19.030, 72.875, 19.055
GRID_HEIGHT = 100
GRID_WIDTH = 100

def generate_dem(output_path):
    # Base elevation around 6.5 meters above sea level
    dem = np.full((GRID_HEIGHT, GRID_WIDTH), 6.5, dtype=np.float32)

    # Coordinates grid
    lats = np.linspace(NORTH, SOUTH, GRID_HEIGHT)
    lons = np.linspace(WEST, EAST, GRID_WIDTH)
    lon_grid, lat_grid = np.meshgrid(lons, lats)

    # 1. Sion Hill / Fort (High ground in the north-east: lat ~19.047, lon ~72.867)
    dist_fort = np.sqrt(((lat_grid - 19.047) / 0.005)**2 + ((lon_grid - 72.867) / 0.005)**2)
    dem += np.maximum(0, 16.0 * np.exp(-dist_fort**2))

    # 2. General slope down towards Mahim Creek / Mithi River in the west (lon ~72.850)
    dem += (lon_grid - 72.850) * 80.0  # +2 meters from west to east

    # 3. Sion Circle Bowl (Known notorious low spot: lat ~19.039, lon ~72.862)
    dist_sion_circle = np.sqrt(((lat_grid - 19.0390) / 0.004)**2 + ((lon_grid - 72.8619) / 0.004)**2)
    dem -= 3.8 * np.exp(-dist_sion_circle**2)

    # 4. Gandhi Market / King's Circle Depression (lat ~19.033, lon ~72.858)
    dist_gandhi_market = np.sqrt(((lat_grid - 19.0335) / 0.004)**2 + ((lon_grid - 72.8580) / 0.004)**2)
    dem -= 3.5 * np.exp(-dist_gandhi_market**2)

    # 5. Road No. 24 Dip (lat ~19.042, lon ~72.857)
    dist_rd24 = np.sqrt(((lat_grid - 19.0420) / 0.003)**2 + ((lon_grid - 72.8570) / 0.003)**2)
    dem -= 2.6 * np.exp(-dist_rd24**2)

    # 6. Smooth micro-topographic variations
    np.random.seed(42)
    noise = np.random.normal(0, 0.15, dem.shape).astype(np.float32)
    dem += noise
    dem = np.maximum(1.5, dem)  # High tide level / minimum ground elevation

    transform = from_bounds(WEST, SOUTH, EAST, NORTH, GRID_WIDTH, GRID_HEIGHT)

    with rasterio.open(
        output_path,
        'w',
        driver='GTiff',
        height=GRID_HEIGHT,
        width=GRID_WIDTH,
        count=1,
        dtype=np.float32,
        crs='EPSG:4326',
        transform=transform,
    ) as dst:
        dst.write(dem, 1)
    print(f"Generated DEM at {output_path}")


def generate_drain_network(output_path):
    # Drainage nodes (inlets, junctions, outfalls)
    # Drain pipes route from high spots & sinks down to Mahim Creek outfalls in the west
    nodes = [
        {"id": "INLET_SION_FORT", "name": "Sion Fort Inlet", "coords": [72.8665, 19.0465], "type": "inlet", "ponding_area_m2": 800},
        {"id": "INLET_SION_STATION_E", "name": "Sion Station East", "coords": [72.8635, 19.0400], "type": "inlet", "ponding_area_m2": 1500},
        {"id": "INLET_SION_CIRCLE", "name": "Sion Circle Sump", "coords": [72.8619, 19.0390], "type": "junction", "ponding_area_m2": 3200},
        {"id": "INLET_LBS_MARG_N", "name": "LBS Marg North Inlet", "coords": [72.8645, 19.0490], "type": "inlet", "ponding_area_m2": 1200},
        {"id": "INLET_LBS_MARG_S", "name": "LBS Marg South Junction", "coords": [72.8630, 19.0440], "type": "junction", "ponding_area_m2": 1800},
        {"id": "INLET_ROAD_24", "name": "Road No. 24 Drain", "coords": [72.8570, 19.0420], "type": "junction", "ponding_area_m2": 2400},
        {"id": "INLET_GANDHI_MKT", "name": "Gandhi Market Low Inlet", "coords": [72.8580, 19.0335], "type": "junction", "ponding_area_m2": 3800},
        {"id": "INLET_KINGS_CIRCLE", "name": "King's Circle Junction", "coords": [72.8560, 19.0315], "type": "junction", "ponding_area_m2": 2600},
        {"id": "INLET_SULO_SHETTY", "name": "Sulochana Shetty Marg", "coords": [72.8600, 19.0450], "type": "inlet", "ponding_area_m2": 1400},
        {"id": "INLET_DHARAVI_LINK", "name": "Dharavi Link Junction", "coords": [72.8540, 19.0400], "type": "junction", "ponding_area_m2": 2000},
        {"id": "OUTFALL_MAHIM_1", "name": "Mahim Creek Outfall North", "coords": [72.8510, 19.0480], "type": "outfall", "ponding_area_m2": 5000},
        {"id": "OUTFALL_MAHIM_2", "name": "Mahim Creek Outfall Central", "coords": [72.8505, 19.0390], "type": "outfall", "ponding_area_m2": 5000},
        {"id": "OUTFALL_MAHIM_3", "name": "Mahim Creek Outfall South", "coords": [72.8515, 19.0320], "type": "outfall", "ponding_area_m2": 5000},
        {"id": "INLET_LTT_CONNECTOR", "name": "LTT Kurla Connector Drain", "coords": [72.8710, 19.0520], "type": "inlet", "ponding_area_m2": 1600},
        {"id": "INLET_EEH_CHUNABHATTI", "name": "EEH Chunabhatti Drain", "coords": [72.8680, 19.0420], "type": "inlet", "ponding_area_m2": 2200},
    ]

    # Pipes connecting nodes:
    # capacity in mm/hr equivalent or m3/s. Sion Circle has 40 mm/hr equiv capacity (as per README example)
    pipes = [
        {"from": "INLET_SION_FORT", "to": "INLET_SION_STATION_E", "capacity_mm_hr": 55.0, "diameter_m": 1.2},
        {"from": "INLET_LBS_MARG_N", "to": "INLET_LBS_MARG_S", "capacity_mm_hr": 50.0, "diameter_m": 1.4},
        {"from": "INLET_LBS_MARG_S", "to": "INLET_SION_CIRCLE", "capacity_mm_hr": 45.0, "diameter_m": 1.5},
        {"from": "INLET_SION_STATION_E", "to": "INLET_SION_CIRCLE", "capacity_mm_hr": 42.0, "diameter_m": 1.4},
        {"from": "INLET_EEH_CHUNABHATTI", "to": "INLET_SION_CIRCLE", "capacity_mm_hr": 48.0, "diameter_m": 1.5},
        {"from": "INLET_SION_CIRCLE", "to": "INLET_ROAD_24", "capacity_mm_hr": 40.0, "diameter_m": 1.6}, # Bottle-neck at Sion Circle
        {"from": "INLET_SULO_SHETTY", "to": "INLET_ROAD_24", "capacity_mm_hr": 45.0, "diameter_m": 1.2},
        {"from": "INLET_ROAD_24", "to": "INLET_DHARAVI_LINK", "capacity_mm_hr": 42.0, "diameter_m": 1.8},
        {"from": "INLET_DHARAVI_LINK", "to": "OUTFALL_MAHIM_2", "capacity_mm_hr": 70.0, "diameter_m": 2.2},
        {"from": "INLET_GANDHI_MKT", "to": "INLET_KINGS_CIRCLE", "capacity_mm_hr": 38.0, "diameter_m": 1.4},
        {"from": "INLET_KINGS_CIRCLE", "to": "OUTFALL_MAHIM_3", "capacity_mm_hr": 60.0, "diameter_m": 2.0},
        {"from": "INLET_LTT_CONNECTOR", "to": "INLET_LBS_MARG_N", "capacity_mm_hr": 50.0, "diameter_m": 1.3},
        {"from": "INLET_SULO_SHETTY", "to": "OUTFALL_MAHIM_1", "capacity_mm_hr": 65.0, "diameter_m": 1.8},
    ]

    features = []
    # Node features
    node_lookup = {n["id"]: n for n in nodes}
    for n in nodes:
        features.append({
            "type": "Feature",
            "id": n["id"],
            "geometry": {
                "type": "Point",
                "coordinates": n["coords"]
            },
            "properties": {
                "id": n["id"],
                "name": n["name"],
                "type": n["type"],
                "ponding_area_m2": n["ponding_area_m2"],
                "feature_class": "drain_node"
            }
        })

    # Pipe features (LineString)
    for p in pipes:
        c1 = node_lookup[p["from"]]["coords"]
        c2 = node_lookup[p["to"]]["coords"]
        features.append({
            "type": "Feature",
            "id": f"PIPE_{p['from']}_TO_{p['to']}",
            "geometry": {
                "type": "LineString",
                "coordinates": [c1, c2]
            },
            "properties": {
                "id": f"PIPE_{p['from']}_TO_{p['to']}",
                "from_node": p["from"],
                "to_node": p["to"],
                "capacity_mm_hr": p["capacity_mm_hr"],
                "diameter_m": p["diameter_m"],
                "feature_class": "drain_pipe"
            }
        })

    geojson_data = {
        "type": "FeatureCollection",
        "features": features
    }

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(geojson_data, f, indent=2)
    print(f"Generated drains GeoJSON at {output_path}")


def generate_road_network(geojson_path, graphml_path):
    # Roads in Sion pilot area connecting key landmarks:
    # Sion Station, Sion Circle, Gandhi Market, Road No. 24, Sulochana Shetty Marg, LTT Hospital route, EEH
    road_segments = [
        {
            "id": "ROAD_SION_CIRCLE",
            "name": "Sion Circle",
            "coords": [[72.8600, 19.0385], [72.8619, 19.0390], [72.8635, 19.0395]],
            "drain_node": "INLET_SION_CIRCLE",
            "base_speed_kmh": 30,
            "highway": "primary"
        },
        {
            "id": "ROAD_NO_24",
            "name": "Road No. 24",
            "coords": [[72.8619, 19.0390], [72.8590, 19.0405], [72.8570, 19.0420]],
            "drain_node": "INLET_ROAD_24",
            "base_speed_kmh": 25,
            "highway": "secondary"
        },
        {
            "id": "ROAD_GANDHI_MKT",
            "name": "Gandhi Market Road (Dr. B.A. Road)",
            "coords": [[72.8600, 19.0385], [72.8590, 19.0360], [72.8580, 19.0335]],
            "drain_node": "INLET_GANDHI_MKT",
            "base_speed_kmh": 35,
            "highway": "primary"
        },
        {
            "id": "ROAD_KINGS_CIRCLE",
            "name": "Maheshwari Udyan / King's Circle",
            "coords": [[72.8580, 19.0335], [72.8570, 19.0325], [72.8560, 19.0315]],
            "drain_node": "INLET_KINGS_CIRCLE",
            "base_speed_kmh": 35,
            "highway": "primary"
        },
        {
            "id": "ROAD_SION_STATION",
            "name": "Sion Station Road",
            "coords": [[72.8619, 19.0390], [72.8635, 19.0400], [72.8650, 19.0410]],
            "drain_node": "INLET_SION_STATION_E",
            "base_speed_kmh": 20,
            "highway": "tertiary"
        },
        {
            "id": "ROAD_SULO_SHETTY",
            "name": "Sulochana Shetty Marg",
            "coords": [[72.8570, 19.0420], [72.8585, 19.0435], [72.8600, 19.0450]],
            "drain_node": "INLET_SULO_SHETTY",
            "base_speed_kmh": 30,
            "highway": "secondary"
        },
        {
            "id": "ROAD_LBS_MARG_S",
            "name": "LBS Marg (Sion Section)",
            "coords": [[72.8619, 19.0390], [72.8625, 19.0415], [72.8630, 19.0440]],
            "drain_node": "INLET_LBS_MARG_S",
            "base_speed_kmh": 35,
            "highway": "primary"
        },
        {
            "id": "ROAD_LBS_MARG_N",
            "name": "LBS Marg North",
            "coords": [[72.8630, 19.0440], [72.8638, 19.0465], [72.8645, 19.0490]],
            "drain_node": "INLET_LBS_MARG_N",
            "base_speed_kmh": 40,
            "highway": "primary"
        },
        {
            "id": "ROAD_DHARAVI_LINK",
            "name": "Dharavi - Sion Link Road",
            "coords": [[72.8570, 19.0420], [72.8555, 19.0410], [72.8540, 19.0400]],
            "drain_node": "INLET_DHARAVI_LINK",
            "base_speed_kmh": 30,
            "highway": "secondary"
        },
        {
            "id": "ROAD_EEH_BYPASS",
            "name": "Eastern Express Highway (Elevated/High Section)",
            "coords": [[72.8650, 19.0410], [72.8670, 19.0430], [72.8690, 19.0460], [72.8710, 19.0500]],
            "drain_node": "INLET_EEH_CHUNABHATTI",
            "base_speed_kmh": 50,
            "highway": "trunk"
        },
        {
            "id": "ROAD_LTT_CONNECTOR",
            "name": "Kurla - LTT Hospital Route",
            "coords": [[72.8645, 19.0490], [72.8680, 19.0510], [72.8728, 19.0535], [72.8826, 19.0728]],
            "drain_node": "INLET_LTT_CONNECTOR",
            "base_speed_kmh": 45,
            "highway": "primary"
        },
        {
            "id": "ROAD_EEH_TO_LTT",
            "name": "EEH to LTT Hospital Bypass",
            "coords": [[72.8710, 19.0500], [72.8750, 19.0540], [72.8826, 19.0728]],
            "drain_node": "INLET_LTT_CONNECTOR",
            "base_speed_kmh": 50,
            "highway": "trunk"
        },
        {
            "id": "ROAD_SION_FORT_CREST",
            "name": "Sion Fort Ridge Road (Elevated Safe Corridor)",
            "coords": [[72.8635, 19.0400], [72.8655, 19.0440], [72.8665, 19.0465], [72.8680, 19.0490], [72.8710, 19.0500]],
            "drain_node": "INLET_SION_FORT",
            "base_speed_kmh": 30,
            "highway": "tertiary"
        }
    ]

    features = []
    G = nx.Graph()

    for road in road_segments:
        coords = road["coords"]
        line = LineString(coords)
        length_km = line.length * 111.0  # approximate degree to km conversion

        features.append({
            "type": "Feature",
            "id": road["id"],
            "geometry": {
                "type": "LineString",
                "coordinates": coords
            },
            "properties": {
                "id": road["id"],
                "name": road["name"],
                "drain_node": road["drain_node"],
                "base_speed_kmh": road["base_speed_kmh"],
                "highway": road["highway"],
                "length_km": round(length_km, 3)
            }
        })

        # Add to graph
        for i in range(len(coords) - 1):
            p1 = coords[i]
            p2 = coords[i+1]
            u = f"{p1[1]:.4f},{p1[0]:.4f}"
            v = f"{p2[1]:.4f},{p2[0]:.4f}"
            seg_dist_km = Point(p1).distance(Point(p2)) * 111.0
            time_hours = seg_dist_km / max(road["base_speed_kmh"], 5)
            time_min = time_hours * 60.0

            G.add_node(u, y=p1[1], x=p1[0])
            G.add_node(v, y=p2[1], x=p2[0])
            G.add_edge(u, v,
                       id=f"{road['id']}_{i}",
                       road_id=road["id"],
                       name=road["name"],
                       drain_node=road["drain_node"],
                       distance_km=float(seg_dist_km),
                       time_min=float(time_min),
                       base_speed_kmh=road["base_speed_kmh"],
                       highway=road["highway"],
                       coords=[p1, p2])

    geojson_data = {
        "type": "FeatureCollection",
        "features": features
    }

    with open(geojson_path, "w", encoding="utf-8") as f:
        json.dump(geojson_data, f, indent=2)
    print(f"Generated roads GeoJSON at {geojson_path}")

    # Write GraphML (for osmnx/networkx loading)
    # Simplify attributes for GraphML compatibility
    G_clean = nx.Graph()
    for n, data in G.nodes(data=True):
        G_clean.add_node(n, y=float(data["y"]), x=float(data["x"]))
    for u, v, data in G.edges(data=True):
        G_clean.add_edge(u, v,
                         id=str(data["id"]),
                         road_id=str(data["road_id"]),
                         name=str(data["name"]),
                         drain_node=str(data["drain_node"]),
                         distance_km=float(data["distance_km"]),
                         time_min=float(data["time_min"]),
                         base_speed_kmh=float(data["base_speed_kmh"]),
                         highway=str(data["highway"]))
    nx.write_graphml(G_clean, graphml_path)
    print(f"Generated roads GraphML at {graphml_path}")


if __name__ == "__main__":
    base_dir = os.path.dirname(os.path.abspath(__file__))
    dem_path = os.path.join(base_dir, "dem.tif")
    drains_path = os.path.join(base_dir, "drains.geojson")
    roads_geojson_path = os.path.join(base_dir, "roads.geojson")
    roads_graphml_path = os.path.join(base_dir, "roads.graphml")

    generate_dem(dem_path)
    generate_drain_network(drains_path)
    generate_road_network(roads_geojson_path, roads_graphml_path)
    print("All pilot data files generated successfully!")
