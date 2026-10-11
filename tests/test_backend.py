"""
test_backend.py - Automated test suite for Urban Flood Nowcasting Backend.
Verifies all model components and Flask API endpoints.
"""
import os
import sys
import json
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import app
from model.terrain import TerrainModel
from model.drainage import DrainageNetwork
from model.flood_model import FloodModel
from model.routing import FloodRouter


class TestUrbanFloodBackend(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = app.test_client()
        cls.base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    def test_01_terrain_model(self):
        dem_path = os.path.join(self.base_dir, "data", "dem.tif")
        terrain = TerrainModel(dem_path)
        self.assertIsNotNone(terrain.elevation)
        self.assertGreater(terrain.height, 0)
        self.assertGreater(terrain.width, 0)
        elev = terrain.get_elevation_at(72.8619, 19.0390)
        self.assertIsInstance(elev, float)
        self.assertGreater(elev, 0.0)

    def test_02_drainage_network(self):
        drains_path = os.path.join(self.base_dir, "data", "drains.geojson")
        drainage = DrainageNetwork(drains_path)
        self.assertGreater(len(drainage.graph.nodes), 0)
        self.assertGreater(len(drainage.graph.edges), 0)

        # Test flow routing
        inflows = {"INLET_SION_CIRCLE": 80.0, "INLET_SION_STATION_E": 60.0}
        res = drainage.route_flow(inflows)
        self.assertIn("node_surcharges", res)
        self.assertIn("pipe_flows", res)

    def test_03_flood_router(self):
        router = FloodRouter()
        # Route from Sion Station to LTT Hospital as per README
        result = router.calculate_safe_route(
            from_coords=[19.0390, 72.8619],
            to_coords=[19.0728, 72.8826],
            time_min=60,
            mode="emergency"
        )
        self.assertIn("from", result)
        self.assertIn("to", result)
        self.assertIn("route_distance_km", result)
        self.assertIn("estimated_time_min", result)
        self.assertIn("avoided", result)
        self.assertGreater(result["route_distance_km"], 0.0)

    def test_04_api_forecast_endpoint(self):
        response = self.client.get("/api/forecast?t=60")
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.data)
        self.assertEqual(data.get("type"), "FeatureCollection")
        self.assertIn("features", data)
        self.assertGreater(len(data["features"]), 0)

    def test_05_api_hotspots_endpoint(self):
        response = self.client.get("/api/hotspots?t=60&min_depth=15")
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.data)
        self.assertIn("hotspots", data)
        self.assertIn("count", data)

    def test_06_api_route_endpoint(self):
        payload = {
            "from": [19.0390, 72.8619],
            "to": [19.0728, 72.8826],
            "t": 60,
            "mode": "emergency"
        }
        response = self.client.post("/api/route",
                                  data=json.dumps(payload),
                                  content_type="application/json")
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.data)
        self.assertEqual(data.get("mode"), "emergency")
        self.assertIn("avoided", data)
        self.assertIn("route_distance_km", data)

    def test_07_api_drain_block_endpoint(self):
        payload = {
            "drain_id": "INLET_SION_CIRCLE",
            "blocked": True,
            "capacity_reduction": 1.0
        }
        response = self.client.post("/api/drain/block",
                                  data=json.dumps(payload),
                                  content_type="application/json")
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.data)
        self.assertEqual(data.get("status"), "success")
        self.assertEqual(data.get("drain_id"), "INLET_SION_CIRCLE")

    def test_08_api_auxiliary_endpoints(self):
        # Rainfall
        resp_rain = self.client.get("/api/rainfall")
        self.assertEqual(resp_rain.status_code, 200)

        # Drains
        resp_drains = self.client.get("/api/drains")
        self.assertEqual(resp_drains.status_code, 200)

        # Roads
        resp_roads = self.client.get("/api/roads")
        self.assertEqual(resp_roads.status_code, 200)

        # Summary
        resp_sum = self.client.get("/api/summary?t=60")
        self.assertEqual(resp_sum.status_code, 200)
        data_sum = json.loads(resp_sum.data)
        self.assertIn("max_depth_cm", data_sum)
        self.assertIn("breakdown", data_sum)

    def test_09_api_route_compare(self):
        payload = {
            "from": [19.0400, 72.8635],
            "to": [19.0728, 72.8826],
            "t": 60,
            "mode": "emergency"
        }
        resp = self.client.post("/api/route/compare",
                                data=json.dumps(payload),
                                content_type="application/json")
        self.assertEqual(resp.status_code, 200)
        data = json.loads(resp.data)
        self.assertIn("safe_route", data)
        self.assertIn("dry_route", data)
        self.assertIn("comparison", data)
        self.assertIn("detour_km", data["comparison"])

    def test_10_api_alerts(self):
        resp = self.client.get("/api/alerts?t=60")
        self.assertEqual(resp.status_code, 200)
        data = json.loads(resp.data)
        self.assertIn("overall_level", data)
        self.assertIn("critical_roads", data)

    def test_11_api_drain_reset_and_status(self):
        # Status
        resp_status = self.client.get("/api/drain/status")
        self.assertEqual(resp_status.status_code, 200)
        data_status = json.loads(resp_status.data)
        self.assertIn("nodes", data_status)
        self.assertIn("pipes", data_status)

        # Reset
        resp_reset = self.client.post("/api/drain/reset")
        self.assertEqual(resp_reset.status_code, 200)
        data_reset = json.loads(resp_reset.data)
        self.assertEqual(data_reset["status"], "success")

    def test_12_frontend_template_routes(self):
        # Main GIS Map
        resp_index = self.client.get("/")
        self.assertEqual(resp_index.status_code, 200)
        self.assertIn(b"Urban Flood Nowcasting", resp_index.data)
        self.assertIn(b"leaflet", resp_index.data.lower())

        # Routing Comparison Page
        resp_routing = self.client.get("/routing")
        self.assertEqual(resp_routing.status_code, 200)
        self.assertIn(b"Flood-Safe Route Planner", resp_routing.data)

        # Tester Console Page
        resp_tester = self.client.get("/tester")
        self.assertEqual(resp_tester.status_code, 200)
        self.assertIn(b"Municipal Simulation Control Room", resp_tester.data)

        # API Tester Page
        resp_api_tester = self.client.get("/api-tester")
        self.assertEqual(resp_api_tester.status_code, 200)
        self.assertIn(b"REST API Test Console", resp_api_tester.data)


if __name__ == "__main__":
    unittest.main()

