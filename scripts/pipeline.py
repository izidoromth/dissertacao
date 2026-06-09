import geopandas as gpd
import osmnx as ox

LOCATION = "Curitiba, Parana, Brazil"
GRID_FORMAT = "hexagon"
CELL_SIZE = 500

def generate_grid(location, grid_format, cell_size):
    city_gdf = ox.geocode_to_gdf(location)
    pass

def main():
    pass

if __name__ == "__main__":
    main()