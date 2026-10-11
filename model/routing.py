"""
Flood-aware routing on an OpenStreetMap road graph.

Uses OSM road geometry and associates the project's 13 forecast roads with nearby
OSM edges. Keeps the existing API response structure.
"""
from __future__ import annotations

import json
import os
from typing import Any

import networkx as nx
import osmnx as ox
from pyproj import Transformer
from shapely.geometry import LineString, Point, shape
from shapely.ops import transform

current_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.dirname(current_dir)

FORECAST_MATCH_RADIUS_M = 25.0
MAX_SNAP_DISTANCE_M = 1500.0
UNKNOWN_FLOOD_TIME_FACTOR = 1.35

_TO_METRES = Transformer.from_crs(
    "EPSG:4326", "EPSG:32643", always_xy=True
).transform


class FloodRouter:
    def __init__(
        self,
        roads_geojson_path: str | None = None,
        graphml_path: str | None = None,
    ):
        self.roads_geojson_path = roads_geojson_path or os.path.join(
            parent_dir, "data", "roads.geojson"
        )
        self.graphml_path = graphml_path or os.path.join(
            parent_dir, "data", "roads.graphml"
        )
        self.forecast_roads_path = os.path.join(
            parent_dir, "data", "roads_original_backup.geojson"
        )

        self.G: nx.MultiDiGraph = nx.MultiDiGraph()
        self.roads_meta: dict[str, dict[str, Any]] = {}
        self.forecast_geometries: dict[str, Any] = {}
        self.edge_forecast: dict[tuple[Any, Any, Any], str] = {}
        self.match_counts: dict[str, int] = {}
        self._build_graph()

    @staticmethod
    def _speed_for_edge(data: dict[str, Any]) -> float:
        speed = data.get("speed_kph")
        if isinstance(speed, (list, tuple)):
            speed = speed[0] if speed else None
        try:
            if speed is not None:
                if isinstance(speed, str):
                    token = speed.lower().replace("km/h", "").replace("kph", "").strip()
                    if "mph" in token:
                        return max(5.0, float(token.replace("mph", "").strip()) * 1.60934)
                    speed = token.split(";")[0].strip()
                return max(5.0, float(speed))
        except (TypeError, ValueError):
            pass

        highway = data.get("highway", "unclassified")
        if isinstance(highway, (list, tuple)):
            highway = highway[0] if highway else "unclassified"
        defaults = {
            "motorway": 70, "motorway_link": 40,
            "trunk": 60, "trunk_link": 35,
            "primary": 40, "primary_link": 30,
            "secondary": 35, "secondary_link": 25,
            "tertiary": 30, "tertiary_link": 20,
            "residential": 20, "living_street": 10,
            "unclassified": 20, "service": 12,
        }
        return float(defaults.get(str(highway), 20))

    @staticmethod
    def _edge_geometry(
        u: Any, v: Any, data: dict[str, Any], graph: nx.MultiDiGraph
    ) -> LineString:
        geom = data.get("geometry")
        if geom is not None and not geom.is_empty:
            return geom
        a, b = graph.nodes[u], graph.nodes[v]
        return LineString([(a["x"], a["y"]), (b["x"], b["y"])])

    def _build_graph(self) -> None:
        if not os.path.exists(self.graphml_path):
            raise FileNotFoundError(f"OSM graph not found: {self.graphml_path}")

        self.G = ox.load_graphml(self.graphml_path)
        if not isinstance(self.G, nx.MultiDiGraph):
            self.G = nx.MultiDiGraph(self.G)

        for u, v, key, data in self.G.edges(keys=True, data=True):
            length_m = float(data.get("length", 0.0) or 0.0)
            if length_m <= 0:
                geom = self._edge_geometry(u, v, data, self.G)
                length_m = transform(_TO_METRES, geom).length
            speed_kph = self._speed_for_edge(data)
            data["dist_km"] = length_m / 1000.0
            data["base_time_min"] = (length_m / 1000.0) / speed_kph * 60.0
            data["route_name"] = data.get("name") or str(data.get("highway", "Road"))
            data["coords"] = list(self._edge_geometry(u, v, data, self.G).coords)
            data["forecast_road_id"] = None

        if os.path.exists(self.forecast_roads_path):
            with open(self.forecast_roads_path, "r", encoding="utf-8-sig") as f:
                original = json.load(f)
            for feat in original.get("features", []):
                props = feat.get("properties", {})
                rid = props.get("id")
                geom_json = feat.get("geometry")
                if not rid or not geom_json:
                    continue
                geom = shape(geom_json)
                self.forecast_geometries[rid] = transform(_TO_METRES, geom)
                self.roads_meta[rid] = {
                    "id": rid,
                    "name": props.get("name") or rid,
                    "drain_node": props.get("drain_node", ""),
                    "speed": float(props.get("base_speed_kmh", 25) or 25),
                    "coords": geom_json.get("coordinates", []),
                }
        else:
            print(f"Warning: forecast-road reference not found: {self.forecast_roads_path}")

        # Match each OSM edge to its nearest forecast line, but only within 25 m.
        for u, v, key, data in self.G.edges(keys=True, data=True):
            edge_geom = transform(
                _TO_METRES, self._edge_geometry(u, v, data, self.G)
            )
            midpoint = edge_geom.interpolate(0.5, normalized=True)
            best_rid = None
            best_distance = float("inf")
            for rid, forecast_geom in self.forecast_geometries.items():
                distance = midpoint.distance(forecast_geom)
                if distance < best_distance:
                    best_distance, best_rid = distance, rid
            if best_rid is not None and best_distance <= FORECAST_MATCH_RADIUS_M:
                data["forecast_road_id"] = best_rid
                self.edge_forecast[(u, v, key)] = best_rid
                self.match_counts[best_rid] = self.match_counts.get(best_rid, 0) + 1

        unmatched = sorted(set(self.roads_meta) - set(self.match_counts))
        print(
            f"Loaded OSM graph: {self.G.number_of_nodes()} nodes, "
            f"{self.G.number_of_edges()} directed edges"
        )
        print(
            f"Forecast road matches: {sum(self.match_counts.values())} edges "
            f"across {len(self.match_counts)}/{len(self.roads_meta)} forecast roads"
        )
        if unmatched:
            print("Forecast roads with no OSM matches:", ", ".join(unmatched))

    def find_nearest_node(self, lat: float, lon: float):
        try:
            node = ox.distance.nearest_nodes(self.G, X=lon, Y=lat)
        except (ImportError, ValueError, nx.NetworkXError):
            return None
        node_data = self.G.nodes[node]
        target_m = transform(_TO_METRES, Point(lon, lat))
        node_m = transform(
            _TO_METRES, Point(float(node_data["x"]), float(node_data["y"]))
        )
        if target_m.distance(node_m) > MAX_SNAP_DISTANCE_M:
            return None
        return node

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
            ("Eastern Express Highway", 19.0500, 72.8710),
            ("Sion Fort", 19.0465, 72.8665),
        ]
        target = transform(_TO_METRES, Point(lon, lat))
        name, distance = min(
            (
                (
                    name,
                    target.distance(
                        transform(_TO_METRES, Point(lon0, lat0))
                    ),
                )
                for name, lat0, lon0 in landmarks
            ),
            key=lambda item: item[1],
        )
        return name if distance <= 2500 else "Location"

    def load_depths_at_time(self, time_min: int) -> dict[str, float]:
        target_t = max(0, min(180, int(round(time_min / 15.0) * 15)))
        depth_file = os.path.join(
            parent_dir, "outputs", f"depths_t{target_t:03d}.geojson"
        )
        depths: dict[str, float] = {}
        if not os.path.exists(depth_file):
            print(f"Warning: flood forecast file not found: {depth_file}")
            return depths
        try:
            with open(depth_file, "r", encoding="utf-8-sig") as f:
                data = json.load(f)
            for feat in data.get("features", []):
                props = feat.get("properties", {})
                rid = props.get("id")
                if rid and "depth_cm" in props:
                    depths[rid] = float(props["depth_cm"])
        except (OSError, ValueError, TypeError, KeyError) as exc:
            print(f"Error reading {depth_file}: {exc}")
        return depths

    def _edge_depth(self, data: dict[str, Any], depths: dict[str, float]):
        rid = data.get("forecast_road_id")
        if rid is None or rid not in depths:
            return None, rid
        return depths[rid], rid

    @staticmethod
    def _mode_threshold(mode: str) -> float:
        if mode == "commuter":
            return 15.0
        if mode == "bus":
            return 35.0
        return 30.0

    @staticmethod
    def _path_edges(graph: nx.MultiDiGraph, path: list[Any]):
        """Select the parallel edge with the lowest active cost for each path step."""
        for u, v in zip(path[:-1], path[1:]):
            parallel = graph.get_edge_data(u, v) or {}
            if not parallel:
                continue
            # Flood-safe graph uses 'weight'; baseline graph uses base travel time.
            cost_key = "weight" if all("weight" in d for d in parallel.values()) else "base_time_min"
            key, data = min(
                parallel.items(),
                key=lambda item: float(item[1].get(cost_key, float("inf"))),
            )
            yield u, v, key, data

    @staticmethod
    def _shortest_path(
        graph: nx.MultiDiGraph, start_node: Any, end_node: Any,
        weight: str = "base_time_min",
    ):
        return nx.shortest_path(graph, start_node, end_node, weight=weight)

    def _make_cost_graph(
        self, depths: dict[str, float], threshold: float | None,
        fallback: bool = False,
    ) -> nx.MultiDiGraph:
        H = nx.MultiDiGraph()
        H.add_nodes_from(self.G.nodes(data=True))
        for u, v, key, data in self.G.edges(keys=True, data=True):
            d_cm, _rid = self._edge_depth(data, depths)
            if d_cm is None:
                cost = data["base_time_min"] * UNKNOWN_FLOOD_TIME_FACTOR
            elif threshold is not None and d_cm >= threshold and not fallback:
                continue
            elif fallback:
                cost = data["base_time_min"] * (1.0 + (d_cm / 3.0) ** 3)
            else:
                cost = data["base_time_min"] * (1.0 + (d_cm / 5.0) ** 2)

            edge_data = dict(data)
            edge_data["weight"] = max(float(cost), 0.001)
            H.add_edge(u, v, key=key, **edge_data)
        return H

    def _route_result(
        self, path: list[Any], graph: nx.MultiDiGraph,
        depths: dict[str, float], threshold: float, used_fallback: bool,
    ):
        coords: list[list[float]] = []
        total_dist_km = 0.0
        total_time_min = 0.0
        max_depth = 0.0
        unknown_edges = 0
        used_roads: set[str] = set()

        for _u, _v, _key, data in self._path_edges(graph, path):
            d_cm, rid = self._edge_depth(data, depths)
            if d_cm is None:
                unknown_edges += 1
                total_time_min += data["base_time_min"] * UNKNOWN_FLOOD_TIME_FACTOR
            else:
                max_depth = max(max_depth, d_cm)
                if rid:
                    used_roads.add(rid)
                speed_mult = max(0.2, 1.0 - d_cm / (threshold + 5.0))
                total_time_min += data["base_time_min"] / speed_mult

            total_dist_km += float(data.get("dist_km", 0.0))
            for xy in data.get("coords", []):
                pair = [float(xy[0]), float(xy[1])]
                if not coords or coords[-1] != pair:
                    coords.append(pair)

        return {
            "coords": coords,
            "distance_km": total_dist_km,
            "time_min": total_time_min,
            "max_depth": max_depth,
            "unknown_edges": unknown_edges,
            "used_roads": used_roads,
        }

    def calculate_safe_route(
        self, from_coords: list, to_coords: list,
        time_min: int = 60, mode: str = "emergency",
    ) -> dict:
        start_lat, start_lon = from_coords
        end_lat, end_lon = to_coords
        start_node = self.find_nearest_node(start_lat, start_lon)
        end_node = self.find_nearest_node(end_lat, end_lon)
        if start_node is None or end_node is None:
            return {"error": "Could not locate valid road nodes within the allowed snapping distance"}

        from_name = self.find_nearest_landmark_name(start_lat, start_lon)
        to_name = self.find_nearest_landmark_name(end_lat, end_lon)
        depths = self.load_depths_at_time(time_min)
        threshold = self._mode_threshold(mode)

        try:
            baseline_path = self._shortest_path(
                self.G, start_node, end_node, "base_time_min"
            )
        except (nx.NetworkXNoPath, nx.NodeNotFound):
            baseline_path = []

        safe_graph = self._make_cost_graph(depths, threshold, fallback=False)
        try:
            safe_path = self._shortest_path(safe_graph, start_node, end_node, "weight")
            chosen_graph = safe_graph
        except (nx.NetworkXNoPath, nx.NodeNotFound):
            fallback_graph = self._make_cost_graph(depths, threshold, fallback=True)
            try:
                safe_path = self._shortest_path(
                    fallback_graph, start_node, end_node, "weight"
                )
                chosen_graph = fallback_graph
                used_fallback = True
            except (nx.NetworkXNoPath, nx.NodeNotFound):
                safe_path = []
                chosen_graph = fallback_graph
                used_fallback = True
        else:
            used_fallback = False

        if not safe_path:
            return {
                "from": from_name, "to": to_name, "mode": mode,
                "status": "No route found in the available road network",
                "avoided": [], "route_distance_km": 0.0,
                "estimated_time_min": 0, "max_depth_on_route_cm": 0.0,
                "route_geojson": None,
            }

        details = self._route_result(
            safe_path, chosen_graph, depths, threshold, used_fallback
        )
        print("DEBUG - Fallback used:", used_fallback)
        print("DEBUG - Maximum depth:", details["max_depth"])
        print("DEBUG - Unknown flood edges:", details["unknown_edges"])
        avoided = sorted({
            f"{self.roads_meta[rid]['name']} ({int(round(d_cm))} cm)"
            for rid, d_cm in depths.items()
            if d_cm >= 15.0 and rid in self.roads_meta
            and rid not in details["used_roads"]
        })

        route_geojson = {
            "type": "Feature",
            "geometry": {"type": "LineString", "coordinates": details["coords"]},
            "properties": {
                "from": from_name, "to": to_name, "mode": mode,
                "distance_km": round(details["distance_km"], 2),
                "estimated_time_min": int(round(details["time_min"])),
                "max_depth_cm": round(details["max_depth"], 1),
                "fallback_taken": used_fallback,
                "unknown_flood_edges": details["unknown_edges"],
            },
        }
        return {
            "from": from_name, "to": to_name, "mode": mode,
            "avoided": avoided,
            "route_distance_km": round(details["distance_km"], 1),
            "estimated_time_min": int(round(details["time_min"])),
            "max_depth_on_route_cm": round(details["max_depth"], 1),
            "unknown_flood_edges": details["unknown_edges"],
            "route_geojson": route_geojson,
        }

    def calculate_route_comparison(
        self, from_coords: list, to_coords: list,
        time_min: int = 60, mode: str = "emergency",
    ) -> dict:
        safe_result = self.calculate_safe_route(
            from_coords, to_coords, time_min=time_min, mode=mode
        )
        if "error" in safe_result:
            return safe_result

        start_lat, start_lon = from_coords
        end_lat, end_lon = to_coords
        start_node = self.find_nearest_node(start_lat, start_lon)
        end_node = self.find_nearest_node(end_lat, end_lon)
        if start_node is None or end_node is None:
            return {"error": "Could not locate valid road nodes within the allowed snapping distance"}

        try:
            baseline_path = self._shortest_path(
                self.G, start_node, end_node, "base_time_min"
            )
        except (nx.NetworkXNoPath, nx.NodeNotFound):
            baseline_path = []

        depths = self.load_depths_at_time(time_min)
        threshold = self._mode_threshold(mode)
        dry_details = self._route_result(
            baseline_path, self.G, depths, threshold, False
        )

        flooded_segments = []
        seen = set()
        for _u, _v, _key, data in self._path_edges(self.G, baseline_path):
            d_cm, _rid = self._edge_depth(data, depths)
            if d_cm is not None and d_cm >= 15.0:
                name = data.get("route_name", "Road")
                identity = (name, round(d_cm, 1))
                if identity not in seen:
                    flooded_segments.append({
                        "name": name,
                        "depth_cm": round(d_cm, 1),
                        "status": "Impassable" if d_cm > threshold else "High Risk",
                    })
                    seen.add(identity)

        from_name = self.find_nearest_landmark_name(start_lat, start_lon)
        to_name = self.find_nearest_landmark_name(end_lat, end_lon)
        dry_geojson = {
            "type": "Feature",
            "geometry": {"type": "LineString", "coordinates": dry_details["coords"]},
            "properties": {
                "from": from_name, "to": to_name, "type": "dry_baseline",
                "distance_km": round(dry_details["distance_km"], 2),
                "estimated_time_min": int(round(dry_details["time_min"])),
                "max_depth_cm": round(dry_details["max_depth"], 1),
                "unknown_flood_edges": dry_details["unknown_edges"],
            },
        }

        detour_km = max(
            0.0, safe_result.get("route_distance_km", 0.0) - dry_details["distance_km"]
        )
        time_penalty_min = max(
            0.0, safe_result.get("estimated_time_min", 0) - dry_details["time_min"]
        )
        depth_saved_cm = max(
            0.0, dry_details["max_depth"] - safe_result.get("max_depth_on_route_cm", 0.0)
        )

        if dry_details["max_depth"] >= 15.0:
            message = (
                f"⚠️ Normal route hits {dry_details['max_depth']:.0f}cm forecast water; "
                "compare the alternative route."
            )
        elif dry_details["unknown_edges"]:
            message = (
                "Normal route has no matched forecast segment at or above 15 cm; "
                "some road segments have unknown flood status."
            )
        else:
            message = "✓ Normal route has no matched forecast segment at or above 15 cm."

        return {
            "from": from_name, "to": to_name, "mode": mode, "time_min": time_min,
            "safe_route": safe_result,
            "dry_route": {
                "distance_km": round(dry_details["distance_km"], 2),
                "estimated_time_min": int(round(dry_details["time_min"])),
                "max_depth_cm": round(dry_details["max_depth"], 1),
                "flooded_segments": flooded_segments,
                "is_severely_flooded": dry_details["max_depth"] >= 30.0,
                "unknown_flood_edges": dry_details["unknown_edges"],
                "route_geojson": dry_geojson,
            },
            "comparison": {
                "detour_km": round(detour_km, 2),
                "time_penalty_min": round(time_penalty_min, 1),
                "depth_saved_cm": round(depth_saved_cm, 1),
                "is_reroute_recommended": dry_details["max_depth"] >= 15.0,
                "message": message,
            },
        }
