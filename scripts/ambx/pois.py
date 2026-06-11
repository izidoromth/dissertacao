"""
Módulo de aquisição de Pontos de Interesse (POIs).

Fornece funções para coletar e categorizar destinos de deslocamento
a partir do OpenStreetMap, com suporte a buffer para capturar
serviços de cidades vizinhas em conurbações.
"""

from __future__ import annotations

from typing import Any

import geopandas as gpd
import osmnx as ox
import pandas as pd
from shapely.geometry import Point

from ambx.utils import utm_crs

# ---------------------------------------------------------------------------
# Categorias padrão de serviços cotidianos
# ---------------------------------------------------------------------------
DEFAULT_CATEGORIES: dict[str, dict[str, Any]] = {
    "health": {
        "amenity": ["hospital", "clinic", "pharmacy", "doctors"],
    },
    "education": {
        "amenity": ["school", "university", "college", "library", "kindergarten"],
    },
    "transportation": {
        "amenity": ["bus_station"],
        "public_transport": ["station", "stop_position"],
        "railway": ["station", "halt"],
    },
    "food": {
        "shop": ["supermarket", "bakery", "convenience", "greengrocer", "butcher"],
        "amenity": ["marketplace"],
    },
}


# ---------------------------------------------------------------------------
# Função principal
# ---------------------------------------------------------------------------
def get_pois(
    location: str,
    buffer: float = 2000,
    categories: dict[str, dict[str, Any]] | None = None,
) -> gpd.GeoDataFrame:
    """
    Coleta Pontos de Interesse do OpenStreetMap para uma localidade.

    A área de busca inclui o contorno administrativo acrescido de um
    ``buffer`` (em metros), permitindo capturar serviços de municípios
    vizinhos acessíveis em conurbações.

    Parameters
    ----------
    location : str
        Nome da localidade (ex.: ``"Curitiba, Parana, Brazil"``).
    buffer : float, default 2000
        Buffer em metros ao redor do contorno administrativo.
        Use ``0`` para restringir aos limites oficiais.
    categories : dict | None, default None
        Dicionário de categorias no formato
        ``{"categoria": {"osm_key": [values]}}``.
        Se ``None``, usa :data:`DEFAULT_CATEGORIES`.

    Returns
    -------
    geopandas.GeoDataFrame
        GeoDataFrame com colunas:

        - ``geometry`` : ``Point`` (EPSG:4326)
        - ``category`` : ``str`` — nome da categoria
        - ``name`` : ``str | None`` — nome do POI (tag ``name`` do OSM)
        - ``osm_tags`` : ``dict`` — dicionário completo de tags OSM
    """
    if categories is None:
        categories = DEFAULT_CATEGORIES

    # 1. Obter polígono da localidade e expandir com buffer
    city_gdf = ox.geocode_to_gdf(location)
    geom = city_gdf.geometry.iloc[0]
    projected_crs = utm_crs(geom)

    if buffer > 0:
        search_poly = (
            gpd.GeoDataFrame(geometry=[geom], crs="EPSG:4326")
            .to_crs(projected_crs)
            .buffer(buffer)
            .to_crs("EPSG:4326")
            .iloc[0]
        )
    else:
        search_poly = geom

    # 2. Baixar features por categoria
    all_pois: list[gpd.GeoDataFrame] = []

    for category_name, tag_filter in categories.items():
        try:
            gdf = ox.features_from_polygon(search_poly, tags=tag_filter)
        except Exception:
            # Categoria pode não retornar nada na área
            continue

        if gdf.empty:
            continue

        # Manter apenas geometrias pontuais e as tags originais
        gdf = gdf.reset_index()
        gdf["category"] = category_name

        # Manter apenas pontos (descartar polígonos de edifícios etc.)
        # features_from_polygon pode retornar relações e ways; puxamos
        # geometria do centróide para polígonos, se houver
        gdf = _to_point_geometries(gdf)

        # Selecionar colunas relevantes
        cols = ["geometry", "category"]
        available_cols = [c for c in ["name", "amenity", "shop", "public_transport",
                                       "railway", "leisure", "tourism"]
                          if c in gdf.columns]
        cols = available_cols + cols

        all_pois.append(gdf[cols].copy())

    if not all_pois:
        return gpd.GeoDataFrame(columns=["geometry", "category", "name"],
                                crs="EPSG:4326")

    # 3. Concatenar e limpar
    result = pd.concat(all_pois, ignore_index=True)
    result = gpd.GeoDataFrame(result, crs="EPSG:4326")

    # Garantir coluna 'name'
    if "name" not in result.columns:
        result["name"] = None

    return result.reset_index(drop=True)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _to_point_geometries(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """
    Garante que todas as geometrias do GeoDataFrame sejam pontos.

    Polígonos e linhas são convertidos para centróides.
    """
    def _ensure_point(geom):
        if geom is None:
            return None
        if geom.geom_type == "Point":
            return geom
        return geom.centroid

    gdf = gdf.copy()
    gdf["geometry"] = gdf["geometry"].apply(_ensure_point)
    # Remove linhas com geometria nula
    gdf = gdf[gdf["geometry"].notna()]
    return gdf
