import osmnx as ox
from pathlib import Path

# Project paths
BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)

GRAPH_PATH = DATA_DIR / "roads.graphml"
EDGES_PATH = DATA_DIR / "roads.geojson"

# Approximate Mumbai bounding box
# Format: north, south, east, west
NORTH = 19.35
SOUTH = 18.85
EAST = 73.05
WEST = 72.75

print("Downloading Mumbai road network from OpenStreetMap...")
print("This may take several minutes.")

try:
    # Download the road network within the bounding box
    G = ox.graph_from_bbox(
        (WEST, SOUTH, EAST, NORTH),
        network_type="drive",
        simplify=True,
        retain_all=False
    )

    # Save the network graph
    ox.save_graphml(G, GRAPH_PATH)

    # Export road segments to GeoJSON
    nodes, edges = ox.graph_to_gdfs(G)
    edges.to_file(EDGES_PATH, driver="GeoJSON")

    print("\nDownload successful!")
    print(f"Nodes: {len(nodes)}")
    print(f"Road segments: {len(edges)}")
    print(f"Graph saved: {GRAPH_PATH}")
    print(f"GeoJSON saved: {EDGES_PATH}")

except Exception as error:
    print("\nDownload failed.")
    print(f"Error: {error}")