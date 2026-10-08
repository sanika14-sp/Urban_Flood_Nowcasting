"""
routing.py - Flood-aware route calculation using A* / Dijkstra algorithms.
Avoids or penalizes road segments inundated above safe depth thresholds based on vehicle mode.
"""
import os
import json
import heapq
import numpy as np
import networkx as nx
from shapely.geometry import Point, LineString

current_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.dirname(current_dir)


class FloodRouter:
    def __init__(self, roads_geojson_path: str = None, graphml_path: str = None):
        self.roads_geojson_path = roads_geojson_path or os.path.join(parent_dir, "data", "roads.geojson")
        self.graphml_path = graphml_path or os.path.join(parent_dir, "data", "roads.graphml")

        self.G = nx.Graph()
        self.roads_meta = {}
        self._build_graph()

    def _build_graph(self):
        """Builds routing graph from roads GeoJSON to ensure clean coordinate mapping."""
        with open(self.roads_geojson_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        for feat in data["features"]:
            props = feat["properties"]
            coords = feat["geometry"]["coordinates"] # list of [lon, lat]
            road_id = props["id"]
            road_name = props["name"]
            speed = float(props.get("base_speed_kmh", 30))
            drain_node = props.get("drain_node", "")

            self.roads_meta[road_id] = {
                "id": road_id,
                "name": road_name,
                "speed": speed,
                "drain_node": drain_node,
                "coords": coords
            }

            for i in range(len(coords) - 1):
                p1 = coords[i]
                p2 = coords[i+1]
                u = f"{p1[1]:.5f},{p1[0]:.5f}"
                v = f"{p2[1]:.5f},{p2[0]:.5f}"

                dist_km = Point(p1).distance(Point(p2)) * 111.0
                time_min = (dist_km / max(speed, 5.0)) * 60.0

                self.G.add_node(u, lat=p1[1], lon=p1[0])
                self.G.add_node(v, lat=p2[1], lon=p2[0])
                self.G.add_edge(
                    u, v,
                    road_id=road_id,
                    name=road_name,
                    drain_node=drain_node,
                    dist_km=dist_km,
                    base_time_min=time_min,
                    coords=[p1, p2]
                )

    def find_nearest_node(self, lat: float, lon: float) -> str:
        best_node = None
        min_dist = float("inf")
        target = Point(lon, lat)

        for n, data in self.G.nodes(data=True):
            pt = Point(data["lon"], data["lat"])
            d = target.distance(pt)
            if d < min_dist:
                min_dist = d
                best_node = n
        return best_node

    def find_nearest_landmark_name(self, lat: float, lon: float) -> str:
        landmarks = [
            ("Sion Station", 19.0400, 72.8635),
            ("Sion Circle", 19.0390, 72.8619),
            ("Gandhi Market", 19.0335, 72.8580),
            ("King's Circle", 19.0315, 72.8560),
            ("Road No. 24", 19.0420, 72.8570),
            ("Sulochana Shetty Marg", 19.0450, 72.8600),
            ("LBS Marg", 19.0440, 72.8630),
            ("Dharavi Link", 19.0400, 72.8540),
            ("LTT Hospital", 19.0728, 72.8826),
            ("Eastern Express Highway", 19.0500, 72.8710)
        ]
        target = Point(lon, lat)
        best_name = "Location"
        min_d = float("inf")
        for name, l_lat, l_lon in landmarks:
            d = target.distance(Point(l_lon, l_lat))
            if d < min_d:
                min_d = d
                best_name = name
        return best_name

    def load_depths_at_time(self, time_min: int) -> dict:
        """
        Loads road depth map at time_min (in cm).
        Returns dict of {road_id: depth_cm}
        """
        depths = {}
        target_t = int(round(time_min / 15.0) * 15)
        target_t = max(0, min(180, target_t))
        depth_file = os.path.join(parent_dir, "outputs", f"depths_t{target_t:03d}.geojson")

        if os.path.exists(depth_file):
            try:
                with open(depth_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                for feat in data.get("features", []):
                    props = feat.get("properties", {})
                    rid = props.get("id")
                    if rid and "depth_cm" in props:
                        depths[rid] = float(props["depth_cm"])
            except Exception as e:
                print(f"Error reading {depth_file}: {e}")

        return depths

    def calculate_safe_route(self,
                             from_coords: list,
                             to_coords: list,
                             time_min: int = 60,
                             mode: str = "emergency") -> dict:
        """
        Computes flood-safe route avoiding impassable depths.
        from_coords: [lat, lon]
        to_coords: [lat, lon]
        time_min: forecast lead time (0 - 180 min)
        mode: 'emergency' (threshold ~ 30-35cm), 'commuter' (threshold ~ 15cm), 'bus' (threshold ~ 35cm)
        """
        start_lat, start_lon = from_coords
        end_lat, end_lon = to_coords

        start_node = self.find_nearest_node(start_lat, start_lon)
        end_node = self.find_nearest_node(end_lat, end_lon)

        if not start_node or not end_node:
            return {"error": "Could not locate valid road nodes for coordinates"}

        from_name = self.find_nearest_landmark_name(start_lat, start_lon)
        to_name = self.find_nearest_landmark_name(end_lat, end_lon)

        depths = self.load_depths_at_time(time_min)

        # Mode thresholds
        if mode == "commuter":
            depth_threshold = 15.0
        elif mode == "bus":
            depth_threshold = 35.0
        else: # emergency / default
            depth_threshold = 30.0

        # Step 1: Run unconstrained shortest path (standard dry conditions)
        try:
            baseline_path = nx.shortest_path(self.G, start_node, end_node, weight="base_time_min")
        except nx.NetworkXNoPath:
            baseline_path = []

        # Step 2: Flood-weighted graph search (A* / Dijkstra)
        # We penalize flooded edges and forbid edges with depth > threshold
        def edge_weight(u, v, data):
            rid = data["road_id"]
            d_cm = depths.get(rid, 0.0)

            if d_cm > depth_threshold:
                return float("inf") # impassable

            # Water slowdown penalty
            delay_factor = 1.0 + (d_cm / 5.0) ** 2
            return data["base_time_min"] * delay_factor

        # Create sub-graph excluding completely impassable edges
        safe_subgraph = nx.Graph()
        impassable_roads_hit = set()

        for u, v, data in self.G.edges(data=True):
            rid = data["road_id"]
            d_cm = depths.get(rid, 0.0)
            if d_cm > depth_threshold:
                impassable_roads_hit.add(f"{data['name']} ({d_cm:.0f} cm)")
                continue

            w = edge_weight(u, v, data)
            safe_subgraph.add_edge(u, v, weight=w, **data)

        # Find safe path
        safe_path = None
        if start_node in safe_subgraph and end_node in safe_subgraph:
            try:
                safe_path = nx.shortest_path(safe_subgraph, start_node, end_node, weight="weight")
            except nx.NetworkXNoPath:
                safe_path = None

        # If impassable, try finding path with high penalty rather than hard cutoff,
        # so emergency services get best available path if completely cut off
        used_fallback = False
        if not safe_path:
            try:
                def penalty_weight(u, v, data):
                    rid = data["road_id"]
                    d_cm = depths.get(rid, 0.0)
                    return data["base_time_min"] * (1.0 + (d_cm / 3.0) ** 3)

                safe_path = nx.shortest_path(self.G, start_node, end_node, weight=penalty_weight)
                used_fallback = True
            except nx.NetworkXNoPath:
                safe_path = []

        if not safe_path:
            return {
                "from": from_name,
                "to": to_name,
                "mode": mode,
                "status": "No passable route available due to severe inundation",
                "avoided": sorted(list(impassable_roads_hit)),
                "route_distance_km": 0.0,
                "estimated_time_min": 0.0,
                "max_depth_on_route_cm": 0.0,
                "route_geojson": None
            }

        # Analyze chosen route
        route_coords = []
        total_dist_km = 0.0
        total_time_min = 0.0
        max_depth = 0.0
        used_roads = set()

        for i in range(len(safe_path) - 1):
            u = safe_path[i]
            v = safe_path[i+1]
            data = self.G.get_edge_data(u, v)

            rid = data["road_id"]
            used_roads.add(rid)
            d_cm = depths.get(rid, 0.0)
            max_depth = max(max_depth, d_cm)

            total_dist_km += data["dist_km"]
            # Time adjusted for depth
            speed_mult = max(0.2, 1.0 - (d_cm / (depth_threshold + 5.0)))
            seg_time = data["base_time_min"] / speed_mult
            total_time_min += seg_time

            for coord in data["coords"]:
                if not route_coords or route_coords[-1] != coord:
                    route_coords.append(coord)

        # Detect avoided flooded streets (present in baseline or nearby but flooded)
        avoided = []
        baseline_roads = set()
        for i in range(len(baseline_path) - 1):
            data = self.G.get_edge_data(baseline_path[i], baseline_path[i+1])
            baseline_roads.add(data["road_id"])

        for rid, d_cm in depths.items():
            if d_cm >= 15.0 and rid in self.roads_meta and rid not in used_roads:
                r_name = self.roads_meta[rid]["name"]
                avoided_str = f"{r_name} ({int(round(d_cm))} cm)"
                if avoided_str not in avoided:
                    avoided.append(avoided_str)

        # Route GeoJSON Feature
        route_geojson = {
            "type": "Feature",
            "geometry": {
                "type": "LineString",
                "coordinates": route_coords
            },
            "properties": {
                "from": from_name,
                "to": to_name,
                "mode": mode,
                "distance_km": round(total_dist_km, 2),
                "estimated_time_min": int(round(total_time_min)),
                "max_depth_cm": round(max_depth, 1),
                "fallback_taken": used_fallback
            }
        }

        return {
            "from": from_name,
            "to": to_name,
            "mode": mode,
            "avoided": avoided,
            "route_distance_km": round(total_dist_km, 1),
            "estimated_time_min": int(round(total_time_min)),
            "max_depth_on_route_cm": int(round(max_depth)),
            "route_geojson": route_geojson
        }
