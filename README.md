# Urban Flood Nowcasting System (Drainage and Rainfall Coupling)

> Rain forecasts tell you how much water will fall. We tell you which streets will flood, how deep, and which route is still safe.

A coupled framework that predicts **street-level inundation 0-3 hours ahead** by fusing rainfall nowcasts, a high-resolution terrain model, and a graph model of the stormwater drainage network. A **Flask (Python)** backend runs the model and serves the results; a **JavaScript** frontend shows them on a live map.

**Pilot area:** one flood-prone neighborhood (e.g., Sion, Mumbai or T. Nagar, Chennai). Configurable.

---

## Problem

Urban flooding in Mumbai, Delhi, and Chennai is an annual crisis. Numerical weather prediction estimates rainfall amounts, but flooding depends on micro-topography, impervious surfaces, and overstrained underground drains. Municipal bodies lack real-time, street-level predictive tools, leading to traffic gridlock, economic loss, and risk to life.

## What this project does

1. **Takes rainfall input** for the next 3 hours (radar or satellite-derived nowcast, or a simulated event).
2. **Routes runoff over terrain** using a DEM to find flow paths and low-lying accumulation zones.
3. **Models the drain network as a directed graph**: manholes and inlets are nodes, pipes and canals are edges with capacity.
4. **Detects surcharge**: where inflow exceeds pipe capacity or a drain is blocked, water spills back onto the street.
5. **Estimates water depth (cm)** per street segment at each time step.
6. **Visualizes** results on a web GIS dashboard with a 0-3 hour time slider.
7. **Suggests flood-safe routes** through an API for emergency services, transit, and commuters.

---

## Architecture

```
 Rainfall nowcast   DEM + land cover   Drain network + roads
        \                 |                  /
         v                v                 v
   +------------ Flask app (Python) ------------+
   |  model/   terrain -> flood_model -> drainage |
   |  app.py   REST API + serves the frontend     |
   |  outputs/ precomputed depth files            |
   +---------------------------------------------+
                      |  REST / JSON
                      v
   +------------ JS frontend (static/) ----------+
   |  Leaflet map | time slider | route panel     |
   +---------------------------------------------+
```

One Flask process does everything: it runs the model, serves the API, and serves the frontend files. There is no database, queue, or container setup to manage. Model results are written to `outputs/` as GeoJSON, and the API reads from there.

---

## Workflow

### Model run (script or on demand)

```
1. Load rainfall         (CSV, satellite, or simulated storm)
2. Runoff                (rain x land-cover coefficient)
3. Surface routing       (D8 flow over DEM to inlets and sinks)
4. Drain graph           (compare inflow with pipe capacity)
5. Surcharge -> depth    (excess volume -> depth in cm on streets)
6. Save                  (outputs/depths_t015.geojson ... t180)
```

### User request flow

1. The browser loads `index.html` and calls `GET /api/forecast?t=0`.
2. Flask returns the matching depth file.
3. Moving the time slider calls the same endpoint with a new `t`, and the JS swaps the layer.
4. Picking a start and end point calls `POST /api/route`. Roads deeper than the threshold are removed or penalized, A* finds the safe path, and the response lists the route and avoided segments.
5. Authorities call `GET /api/hotspots` for surcharging nodes ranked by depth.

---

## Method

| Stage | Approach |
|---|---|
| Rainfall | Time series of rain intensity (mm/h) in 10-15 min steps |
| Runoff | Rainfall x runoff coefficient (high for paved areas, lower for green/open land) |
| Surface flow | D8 flow direction and flow accumulation from DEM; sinks identified as ponding locations |
| Drain network | `networkx` DiGraph; each edge has capacity (m3/s or mm/h equivalent) |
| Overflow | If inflow at a node exceeds outflow capacity, excess volume spills to the surface |
| Depth | Excess volume divided by ponding area of the low spot gives depth (cm) |
| Routing | Road graph from OSM; edges deeper than a threshold (default 30 cm) are removed or heavily penalized |

### Depth categories

| Depth | Level | Meaning |
|---|---|---|
| < 5 cm | Green | Passable |
| 5-15 cm | Yellow | Slow traffic |
| 15-30 cm | Orange | Small vehicles at risk |
| > 30 cm | Red | Avoid, impassable for most vehicles |

---

## Tech stack

| Layer | Tool |
|---|---|
| Terrain | SRTM 30 m or Cartosat DEM, `pysheds` or `richdem`, `rasterio` |
| Drain graph | `networkx`, `geopandas`, `shapely` |
| Road network | OpenStreetMap via `osmnx` |
| Rainfall | IMD / GPM satellite rainfall, or simulated storm event |
| Backend / API | **Flask**, Flask-CORS |
| Frontend | **Vanilla JavaScript**, Leaflet, HTML/CSS |
| Storage | Plain files (GeoJSON, GeoTIFF, CSV) |

---

## Project structure

```
urban-flood-nowcast/
├── README.md
├── requirements.txt
├── app.py                  # Flask app: API routes + serves static/
│
├── model/
│   ├── terrain.py          # flow direction, accumulation, sinks
│   ├── drainage.py         # drain graph, capacity, surcharge
│   ├── flood_model.py      # runoff + depth per time step (run as script)
│   └── routing.py          # flood-aware route calculation
│
├── data/
│   ├── dem.tif             # clipped DEM for the pilot area
│   ├── drains.geojson      # synthetic or municipal drain network
│   ├── roads.graphml       # road network (osmnx)
│   └── rainfall_event.csv  # time, intensity_mm_per_hr
│
├── outputs/                # generated: depths_t000.geojson, t015, ... t180
│
└── static/                 # frontend, served by Flask
    ├── index.html
    ├── style.css
    └── app.js              # map, time slider, route panel, hotspots
```

14 files in total. If `app.js` grows too large, split it into `map.js`, `slider.js`, and `routes.js`.

---

## Setup

```bash
git clone <your-repo-url>
cd urban-flood-nowcast
python -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

`requirements.txt`
```
numpy
pandas
geopandas
shapely
networkx
osmnx
rasterio
pysheds
flask
flask-cors
```

## Run

```bash
# 1. Run the flood model for a rain event (writes files to outputs/)
python model/flood_model.py --rain data/rainfall_event.csv

# 2. Start the app (API + dashboard)
flask --app app run --debug

# 3. Open http://localhost:5000
```

To refresh forecasts automatically, run step 1 every 10 minutes with `cron` or Task Scheduler. For the demo, re-run it by hand.

## API endpoints

| Endpoint | Description |
|---|---|
| `GET /api/forecast?t=<minutes>` | Predicted street depths at a given lead time (0-180 min) |
| `GET /api/hotspots?t=<minutes>&min_depth=15` | Surcharging nodes and streets above a depth threshold |
| `POST /api/route` | Flood-safe route avoiding flooded streets |
| `POST /api/drain/block` | Mark a drain as blocked and re-run the model |

Example route request and response:

```json
// POST /api/route
{ "from": [19.0390, 72.8619], "to": [19.0728, 72.8826], "t": 60, "mode": "emergency" }
```

```json
{
  "from": "Sion Station",
  "to": "LTT Hospital",
  "mode": "emergency",
  "avoided": ["Sion Circle (35 cm)", "Road No. 24 (22 cm)"],
  "route_distance_km": 4.2,
  "estimated_time_min": 18,
  "max_depth_on_route_cm": 3
}
```

---

## Example scenario

- **Radar nowcast:** 60 mm/h rain expected in 45 minutes.
- **Terrain:** the junction sits in a low-lying bowl receiving runoff from three roads.
- **Drain graph:** the junction's pipe carries 40 mm/h equivalent, so it surcharges.
- **Output:** about 35 cm of water at 6:30 PM, lasting around 90 minutes. The routing API sends ambulances via an alternate road.

---

## Suggested build order

1. `terrain.py` in a notebook: DEM, runoff, and flow routing on synthetic rain. Confirm that low spots collect water.
2. `drainage.py` with a hand-made 20-node network. Confirm that blocking a drain raises depth.
3. `flood_model.py` writes depth files; `app.py` serves `/api/forecast`; show a static layer on the map.
4. Add the time slider.
5. Add `routing.py` and the route panel.
6. Add hotspots and the block-drain toggle for the live demo.

## Scaling up later

When the prototype needs to grow, the lightweight setup can be swapped piece by piece: GeoJSON files to PostGIS, manual or cron runs to Celery Beat, and local hosting to a small cloud VM (EC2 or Lightsail) with S3 for static files. Keep the model code in `model/` free of Flask imports so it moves unchanged.

---

## Limitations

- Real municipal drainage maps are rarely public, so the pilot uses a **simplified or synthetic drain network**. Depth values are illustrative, not validated predictions.
- Real Doppler radar feeds may not be accessible, so the demo uses satellite rainfall or a simulated storm.
- The model uses a simplified hydraulic approach (capacity-based overflow), not a full dynamic simulation like SWMM.
- Not calibrated against observed flood records.

## Future scope

- Radar-based rainfall nowcasting (optical flow or deep learning)
- Full hydraulic modelling with SWMM or 2D shallow-water solvers
- Real drainage data and blockage reports from municipal corporations
- Calibration using historical flood complaints and traffic data
- Integration with live navigation apps and traffic police dashboards
- City-wide scaling and mobile alerts

---

## Team

| Name | Role |
|---|---|
| _Your name_ | _Role_ |

## License

MIT
