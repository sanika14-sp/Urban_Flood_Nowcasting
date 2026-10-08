"""
terrain.py - Terrain analysis, flow routing, sinks, and runoff calculation.
Uses DEM (rasterio), D8 flow direction, flow accumulation, and rational runoff coefficients.
"""
import os
import numpy as np
import rasterio
from scipy.ndimage import generic_filter, minimum_filter


class TerrainModel:
    def __init__(self, dem_path: str):
        self.dem_path = dem_path
        if not os.path.exists(dem_path):
            raise FileNotFoundError(f"DEM raster file not found at: {dem_path}")

        with rasterio.open(dem_path) as src:
            self.elevation = src.read(1).astype(np.float32)
            self.transform = src.transform
            self.crs = src.crs
            self.bounds = src.bounds
            self.height, self.width = self.elevation.shape
            self.res_x = abs(self.transform.a)
            self.res_y = abs(self.transform.e)

        # Precompute topographic derivatives
        self.flow_dir, self.slopes = self._calculate_d8_flow()
        self.flow_acc = self._calculate_flow_accumulation()
        self.sinks = self._identify_sinks()

    def _calculate_d8_flow(self):
        """
        D8 flow routing:
        Directions encoded as:
        [NW=7, N=0, NE=1]
        [ W=6,    ,  E=2]
        [SW=5, S=4, SE=3]
        """
        h, w = self.height, self.width
        flow_dir = np.full((h, w), -1, dtype=np.int32)
        slopes = np.zeros((h, w), dtype=np.float32)

        # Direction offsets: (dy, dx, distance_multiplier)
        offsets = [
            (-1,  0, 1.0),      # 0: North
            (-1,  1, np.sqrt(2)), # 1: NE
            ( 0,  1, 1.0),      # 2: East
            ( 1,  1, np.sqrt(2)), # 3: SE
            ( 1,  0, 1.0),      # 4: South
            ( 1, -1, np.sqrt(2)), # 5: SW
            ( 0, -1, 1.0),      # 6: West
            (-1, -1, np.sqrt(2))  # 7: NW
        ]

        # Padding elevation for boundary safety
        padded = np.pad(self.elevation, 1, mode='edge')

        max_drop = np.zeros((h, w), dtype=np.float32)

        for d_idx, (dy, dx, dist_mult) in enumerate(offsets):
            neighbor = padded[1 + dy : 1 + dy + h, 1 + dx : 1 + dx + w]
            dist_meters = dist_mult * ((self.res_x + self.res_y) / 2.0 * 111000.0)
            dist_meters = max(dist_meters, 1.0)
            drop = (self.elevation - neighbor) / dist_meters

            steeper = drop > max_drop
            max_drop[steeper] = drop[steeper]
            flow_dir[steeper] = d_idx
            slopes[steeper] = drop[steeper]

        return flow_dir, slopes

    def _calculate_flow_accumulation(self):
        """
        Computes the number of upslope cells draining into each cell using topological sorting.
        """
        h, w = self.height, self.width
        in_degree = np.zeros((h, w), dtype=np.int32)
        flow_acc = np.ones((h, w), dtype=np.float32)

        offsets = [
            (-1,  0), ( -1,  1), ( 0,  1), ( 1,  1),
            ( 1,  0), (  1, -1), ( 0, -1), (-1, -1)
        ]

        # Count in-degrees
        for r in range(h):
            for c in range(w):
                d = self.flow_dir[r, c]
                if d >= 0:
                    dy, dx = offsets[d]
                    nr, nc = r + dy, c + dx
                    if 0 <= nr < h and 0 <= nc < w:
                        in_degree[nr, nc] += 1

        # Topological sorting queue (cells with in_degree == 0)
        queue = [(r, c) for r in range(h) for c in range(w) if in_degree[r, c] == 0]

        while queue:
            r, c = queue.pop(0)
            d = self.flow_dir[r, c]
            if d >= 0:
                dy, dx = offsets[d]
                nr, nc = r + dy, c + dx
                if 0 <= nr < h and 0 <= nc < w:
                    flow_acc[nr, nc] += flow_acc[r, c]
                    in_degree[nr, nc] -= 1
                    if in_degree[nr, nc] == 0:
                        queue.append((nr, nc))

        return flow_acc

    def _identify_sinks(self):
        """
        Identifies topographic depressions/sinks (local minima) where water ponds.
        """
        min_elev = minimum_filter(self.elevation, size=3, mode='reflect')
        # Sinks have elevation equal to local minimum
        sinks = (self.elevation == min_elev) & (self.flow_dir == -1)
        return sinks

    def coord_to_cell(self, lon: float, lat: float):
        """Converts geographic (lon, lat) to raster (row, col)."""
        col = int((lon - self.bounds.left) / (self.bounds.right - self.bounds.left) * self.width)
        row = int((self.bounds.top - lat) / (self.bounds.top - self.bounds.bottom) * self.height)
        row = np.clip(row, 0, self.height - 1)
        col = np.clip(col, 0, self.width - 1)
        return row, col

    def cell_to_coord(self, row: int, col: int):
        """Converts raster (row, col) to geographic (lon, lat)."""
        lon = self.bounds.left + (col + 0.5) / self.width * (self.bounds.right - self.bounds.left)
        lat = self.bounds.top - (row + 0.5) / self.height * (self.bounds.top - self.bounds.bottom)
        return lon, lat

    def get_elevation_at(self, lon: float, lat: float) -> float:
        r, c = self.coord_to_cell(lon, lat)
        return float(self.elevation[r, c])

    def calculate_runoff(self, rainfall_intensity_mm_hr: float, land_cover_coeff: float = 0.85) -> np.ndarray:
        """
        Runoff = Rainfall x Runoff Coefficient
        Urban paved areas typically 0.80 - 0.90; open/green spaces 0.30 - 0.50.
        Returns runoff in mm/hr for each cell.
        """
        # Impervious surface coefficient: lower near high fort/greens, high in urban core
        h, w = self.height, self.width
        coeff_grid = np.full((h, w), land_cover_coeff, dtype=np.float32)

        # Sion Fort / Ridge area has more vegetation/open ground
        fort_r, fort_c = self.coord_to_cell(72.8665, 19.0465)
        y, x = np.ogrid[:h, :w]
        dist_sq = (y - fort_r)**2 + (x - fort_c)**2
        coeff_grid[dist_sq < 100] = 0.40  # Hill park area

        runoff_mm_hr = rainfall_intensity_mm_hr * coeff_grid
        return runoff_mm_hr

    def get_node_catchment_runoff(self, node_coords_dict: dict, rainfall_intensity_mm_hr: float) -> dict:
        """
        Maps distributed runoff from the terrain grid to drainage inlet nodes based on
        proximity and upslope flow accumulation.
        """
        runoff_grid = self.calculate_runoff(rainfall_intensity_mm_hr)
        node_inflows = {node_id: 0.0 for node_id in node_coords_dict}

        node_keys = list(node_coords_dict.keys())
        if not node_keys:
            return node_inflows

        # Precompute coordinate grids
        r_grid, c_grid = np.ogrid[:self.height, :self.width]

        # Stack distances to all nodes
        dist_stack = []
        for nid in node_keys:
            coords = node_coords_dict[nid]
            nr, nc = self.coord_to_cell(coords[0], coords[1])
            d_sq = (r_grid - nr)**2 + (c_grid - nc)**2
            dist_stack.append(d_sq)

        dist_stack = np.stack(dist_stack, axis=-1) # (H, W, num_nodes)
        closest_indices = np.argmin(dist_stack, axis=-1) # (H, W)

        # Weighted cell runoff by flow accumulation
        weighted_runoff = runoff_grid * (1.0 + np.log1p(self.flow_acc) / 25.0)

        # Aggregate per node
        scale = len(node_keys) / max(self.height * self.width, 1) * 3.5
        for idx, nid in enumerate(node_keys):
            mask = (closest_indices == idx)
            total = float(np.sum(weighted_runoff[mask])) * scale
            node_inflows[nid] = round(max(0.0, total), 2)

        return node_inflows

