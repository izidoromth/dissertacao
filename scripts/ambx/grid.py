"""
Módulo de geração de malha territorial.

Fornece funções para criar grades regulares (quadradas ou hexagonais)
que servem como unidade mínima de análise territorial, recortadas
pelo contorno de uma localidade.
"""

import math
from enum import Enum

import geopandas as gpd
import numpy as np
import osmnx as ox
from shapely import Polygon, intersects
from shapely.geometry import box

from ambx.utils import utm_crs


class GridFormat(Enum):
    """Formato da célula da malha de análise."""

    SQUARE = "square"
    HEXAGON = "hexagon"


def _create_hexagon(radius: float, center_x: float, center_y: float) -> Polygon:
    """
    Cria um hexágono regular como um ``shapely.Polygon``.

    Parameters
    ----------
    radius : float
        Raio do hexágono (distância do centro a cada vértice).
    center_x : float
        Coordenada X do centro.
    center_y : float
        Coordenada Y do centro.

    Returns
    -------
    Polygon
        Polígono representando o hexágono.
    """
    angles = range(0, 360, 60)
    coords = [
        [
            center_x + math.cos(math.radians(angle)) * radius,
            center_y + math.sin(math.radians(angle)) * radius,
        ]
        for angle in angles
    ]
    return Polygon(coords)


def _generate_square_cells(minx: float, miny: float, maxx: float, maxy: float,
                           cell_size: float) -> list:
    """
    Gera células quadradas cobrindo a extensão delimitada.

    Parameters
    ----------
    minx, miny, maxx, maxy : float
        Extensão do bounding box.
    cell_size : float
        Lado do quadrado (na unidade do CRS).

    Returns
    -------
    list of Polygon
    """
    xs = np.arange(minx, maxx, cell_size)
    ys = np.arange(miny, maxy, cell_size)
    xx, yy = np.meshgrid(xs, ys)
    return [box(x, y, x + cell_size, y + cell_size)
            for x, y in zip(xx.ravel(), yy.ravel())]


def _generate_hex_cells(minx: float, miny: float, maxx: float, maxy: float,
                        cell_size: float) -> list:
    """
    Gera células hexagonais cobrindo a extensão delimitada.

    O parâmetro ``cell_size`` é interpretado como o raio do hexágono.

    Parameters
    ----------
    minx, miny, maxx, maxy : float
        Extensão do bounding box.
    cell_size : float
        Raio do hexágono (na unidade do CRS).

    Returns
    -------
    list of Polygon
    """
    rows = math.ceil((maxy - miny) / (cell_size * math.sin(math.radians(60))))
    cols = math.ceil((maxx - minx) / (3 * cell_size))
    cells = []

    for i in range(rows):
        for j in range(cols):
            center_x = minx + j * 3 * cell_size
            if i % 2 == 1:
                center_x += 1.5 * cell_size
            center_y = miny + i * cell_size * math.sin(math.radians(60))
            cells.append(_create_hexagon(cell_size, center_x, center_y))

    return cells


def generate_grid(location: str, grid_format: GridFormat = GridFormat.HEXAGON,
                  cell_size: float = 500) -> gpd.GeoDataFrame:
    """
    Gera uma malha territorial para a localidade especificada.

    A malha é criada no CRS UTM adequado à localização, recortada pelo
    contorno administrativo e reprojetada para WGS84 (EPSG:4326).

    Parameters
    ----------
    location : str
        Nome da localidade (ex.: ``"Curitiba, Parana, Brazil"``), usado
        pelo OSMnx para geocodificação.
    grid_format : GridFormat, default HEXAGON
        Formato das células: ``GridFormat.SQUARE`` ou ``GridFormat.HEXAGON``.
    cell_size : float, default 500
        Tamanho da célula em metros:
        - Quadrado: lado do quadrado.
        - Hexágono: raio do hexágono.

    Returns
    -------
    geopandas.GeoDataFrame
        GeoDataFrame com as células da malha em EPSG:4326.
        A coluna ``geometry`` contém os polígonos das células.
    """
    # Geocodificar e projetar para UTM
    city_gdf = ox.geocode_to_gdf(location)
    geom = city_gdf.geometry.iloc[0]
    projected = city_gdf.to_crs(utm_crs(geom))
    poly = projected.geometry.iloc[0]
    minx, miny, maxx, maxy = projected.total_bounds

    # Gerar células no formato escolhido
    if grid_format == GridFormat.HEXAGON:
        cells = _generate_hex_cells(minx, miny, maxx, maxy, cell_size)
    else:
        cells = _generate_square_cells(minx, miny, maxx, maxy, cell_size)

    # Recortar pelo contorno da cidade
    mask = intersects(cells, poly)
    grid = gpd.GeoDataFrame(
        geometry=np.array(cells)[mask],
        crs=projected.crs,
    )

    # Reprojetar para WGS84
    grid = grid.to_crs("EPSG:4326")
    return grid
