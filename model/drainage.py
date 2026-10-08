"""
drainage.py - Drain network graph model, capacity constraints, blockage simulation, and surcharge calculation.
Uses networkx DiGraph to model manholes, inlets, and pipes.
"""
import os
import json
import networkx as nx


class DrainageNetwork:
    def __init__(self, geojson_path: str):
        self.geojson_path = geojson_path
        if not os.path.exists(geojson_path):
            raise FileNotFoundError(f"Drain network GeoJSON not found at: {geojson_path}")

        self.graph = nx.DiGraph()
        self.nodes_data = {}
        self.pipes_data = {}
        self.blocked_items = {} # id -> capacity_reduction_factor (1.0 = completely blocked)

        self._load_network()

    def _load_network(self):
        with open(self.geojson_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        for feat in data.get("features", []):
            f_class = feat.get("properties", {}).get("feature_class")
            props = feat.get("properties", {})
            geom = feat.get("geometry", {})

            if f_class == "drain_node":
                node_id = props["id"]
                coords = geom.get("coordinates", [0, 0])
                self.nodes_data[node_id] = {
                    "id": node_id,
                    "name": props.get("name", node_id),
                    "type": props.get("type", "inlet"),
                    "ponding_area_m2": props.get("ponding_area_m2", 1500.0),
                    "coords": coords, # [lon, lat]
                }
                self.graph.add_node(
                    node_id,
                    name=props.get("name", node_id),
                    type=props.get("type", "inlet"),
                    ponding_area_m2=props.get("ponding_area_m2", 1500.0),
                    lon=coords[0],
                    lat=coords[1]
                )

            elif f_class == "drain_pipe":
                pipe_id = props["id"]
                u = props["from_node"]
                v = props["to_node"]
                cap = float(props.get("capacity_mm_hr", 45.0))
                dia = float(props.get("diameter_m", 1.2))

                self.pipes_data[pipe_id] = {
                    "id": pipe_id,
                    "from_node": u,
                    "to_node": v,
                    "base_capacity": cap,
                    "diameter_m": dia
                }
                self.graph.add_edge(
                    u, v,
                    id=pipe_id,
                    base_capacity=cap,
                    diameter_m=dia
                )

    def set_blockage(self, item_id: str, blocked: bool = True, reduction: float = 1.0):
        """
        Marks a drain pipe or node as blocked with given capacity reduction factor (0.0 to 1.0).
        1.0 means 100% blocked (capacity reduced to 0).
        """
        if blocked:
            self.blocked_items[item_id] = max(0.0, min(1.0, float(reduction)))
        else:
            self.blocked_items.pop(item_id, None)

    def is_blocked(self, item_id: str) -> bool:
        return item_id in self.blocked_items

    def get_effective_capacity(self, u: str, v: str) -> float:
        edge_data = self.graph.get_edge_data(u, v)
        if not edge_data:
            return 0.0

        pipe_id = edge_data.get("id")
        base_cap = edge_data.get("base_capacity", 45.0)

        # Check edge blockage
        reduction = self.blocked_items.get(pipe_id, 0.0)

        # Check if source or target node is blocked
        node_reduction = max(
            self.blocked_items.get(u, 0.0),
            self.blocked_items.get(v, 0.0)
        )
        total_reduction = max(reduction, node_reduction)

        effective_cap = base_cap * (1.0 - total_reduction)
        return max(0.0, effective_cap)

    def route_flow(self, surface_inflows: dict) -> dict:
        """
        Calculates hydraulic routing down the network.
        surface_inflows: dict of {node_id: inflow_rate_mm_hr}

        Returns:
        {
            "node_surcharges": {node_id: surcharge_rate_mm_hr},
            "total_inflows": {node_id: total_inflow},
            "pipe_flows": {pipe_id: flow_rate},
            "pipe_utilizations": {pipe_id: fraction_of_capacity},
            "surcharging_nodes": list of {node_id, name, coords, surcharge_rate, depth_factor}
        }
        """
        node_inflow = {n: float(surface_inflows.get(n, 0.0)) for n in self.graph.nodes}
        node_surcharges = {n: 0.0 for n in self.graph.nodes}
        pipe_flows = {}
        pipe_utilizations = {}

        # Topological traversal (or in-degree order)
        try:
            eval_order = list(nx.topological_sort(self.graph))
        except nx.NetworkXUnfeasible:
            eval_order = list(self.graph.nodes)

        for u in eval_order:
            curr_inflow = node_inflow[u]
            out_edges = list(self.graph.out_edges(u, data=True))

            if not out_edges:
                # Outfall node or sink - discharges cleanly into creek/river unless surcharging
                if self.nodes_data[u]["type"] == "outfall":
                    node_surcharges[u] = 0.0
                else:
                    node_surcharges[u] = curr_inflow
                continue

            total_out_capacity = sum(self.get_effective_capacity(u, v) for _, v, _ in out_edges)

            if total_out_capacity <= 0:
                # All outgoing pipes completely blocked
                node_surcharges[u] = curr_inflow
                for _, v, data in out_edges:
                    p_id = data["id"]
                    pipe_flows[p_id] = 0.0
                    pipe_utilizations[p_id] = 1.0
            elif curr_inflow > total_out_capacity:
                # Surcharge! Inflow exceeds capacity
                surcharge = curr_inflow - total_out_capacity
                node_surcharges[u] = surcharge

                # Each pipe runs at full capacity
                for _, v, data in out_edges:
                    p_id = data["id"]
                    eff_cap = self.get_effective_capacity(u, v)
                    pipe_flows[p_id] = eff_cap
                    pipe_utilizations[p_id] = 1.0
                    node_inflow[v] += eff_cap
            else:
                # Flow fits in pipe capacity
                node_surcharges[u] = 0.0
                for _, v, data in out_edges:
                    p_id = data["id"]
                    eff_cap = self.get_effective_capacity(u, v)
                    # Distribute proportionally to pipe capacities
                    share = (eff_cap / total_out_capacity) * curr_inflow
                    pipe_flows[p_id] = share
                    pipe_utilizations[p_id] = round(share / max(eff_cap, 0.01), 3)
                    node_inflow[v] += share

        # Collect surcharging nodes
        surcharging_nodes = []
        for n, surch in node_surcharges.items():
            if surch > 0.1:
                nd = self.nodes_data[n]
                surcharging_nodes.append({
                    "id": n,
                    "name": nd["name"],
                    "coords": nd["coords"],
                    "surcharge_rate_mm_hr": round(surch, 2),
                    "total_inflow": round(node_inflow[n], 2),
                    "ponding_area_m2": nd["ponding_area_m2"]
                })

        return {
            "node_surcharges": node_surcharges,
            "total_inflows": node_inflow,
            "pipe_flows": pipe_flows,
            "pipe_utilizations": pipe_utilizations,
            "surcharging_nodes": surcharging_nodes
        }

    def get_all_nodes(self):
        return self.nodes_data

    def get_all_pipes(self):
        return self.pipes_data
