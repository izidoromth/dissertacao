"""
Módulo de construção e manipulação da rede viária.

Fornece funções para obter o grafo de vias a partir do OpenStreetMap,
projetá-lo para um CRS métrico e vincular os centróides da malha
territorial aos nós mais próximos da rede.
"""

from __future__ import annotations

import geopandas as gpd
import networkx as nx
import osmnx as ox
import pandas as pd
from shapely.geometry import Point

from ambx.utils import utm_crs


# ---------------------------------------------------------------------------
# Construção do grafo
# ---------------------------------------------------------------------------
def get_network(
    location: str,
    network_type: str = "walk",
    custom_filter: str | None = None,
) -> nx.MultiDiGraph:
    """
    Constrói o grafo viário a partir do OpenStreetMap.

    Utiliza ``osmnx.graph_from_place`` para baixar a rede de
    deslocamento e a retorna como um ``networkx.MultiDiGraph``
    no CRS original do OSM (EPSG:4326).

    Parameters
    ----------
    location : str
        Nome da localidade (ex.: ``"Curitiba, Parana, Brazil"``).
    network_type : str, default "walk"
        Tipo de rede a ser baixada:
        ``"walk"``, ``"bike"``, ``"drive"``, ``"all"``,
        ``"all_private"`` ou ``"drive_service"``.
    custom_filter : str | None, default None
        Filtro OSM customizado no formato
        ``'["highway"~"primary|secondary"]'``.
        Se informado, sobrescreve ``network_type``.

    Returns
    -------
    networkx.MultiDiGraph
        Grafo viário em EPSG:4326. As arestas contêm o atributo
        ``length`` (em metros) e demais metadados OSM.
    """
    return ox.graph_from_place(
        location,
        network_type=network_type,
        custom_filter=custom_filter,
    )


def get_network_from_polygon(
    polygon: gpd.GeoDataFrame | gpd.GeoSeries,
    network_type: str = "walk",
    custom_filter: str | None = None,
) -> nx.MultiDiGraph:
    """
    Constrói o grafo viário a partir de um polígono.

    Alternativa a :func:`get_network` quando já se dispõe da
    geometria do contorno.

    Parameters
    ----------
    polygon : GeoDataFrame ou GeoSeries
        Polígono de contorno da área de interesse.
    network_type : str, default "walk"
        Tipo de rede (mesmos valores de :func:`get_network`).
    custom_filter : str | None, default None
        Filtro OSM customizado.

    Returns
    -------
    networkx.MultiDiGraph
        Grafo viário em EPSG:4326.
    """
    if isinstance(polygon, gpd.GeoDataFrame):
        polygon = polygon.geometry

    return ox.graph_from_polygon(
        polygon.union_all(),
        network_type=network_type,
        custom_filter=custom_filter,
    )


# ---------------------------------------------------------------------------
# Projeção do grafo
# ---------------------------------------------------------------------------
def project_network(
    graph: nx.MultiDiGraph,
    to_crs: str | None = None,
) -> nx.MultiDiGraph:
    """
    Projeta o grafo viário para um CRS métrico (UTM).

    Parameters
    ----------
    graph : networkx.MultiDiGraph
        Grafo em EPSG:4326 (saída de :func:`get_network`).
    to_crs : str | None, default None
        CRS de destino (ex.: ``"EPSG:32722"``).
        Se ``None``, determina automaticamente a zona UTM adequada
        a partir da geometria do grafo.

    Returns
    -------
    networkx.MultiDiGraph
        Grafo projetado. Os atributos ``x`` e ``y`` dos nós são
        atualizados para as coordenadas projetadas. O atributo
        ``length`` das arestas é recalculado.
    """
    if to_crs is None:
        # Extrai os centróides dos nós para determinar a zona UTM
        nodes_gdf = ox.graph_to_gdfs(graph, nodes=True, edges=False)
        to_crs = utm_crs(nodes_gdf.geometry.union_all())

    return ox.project_graph(graph, to_crs=to_crs)


# ---------------------------------------------------------------------------
# Vinculação da malha à rede (snapping)
# ---------------------------------------------------------------------------
def snap_grid_to_network(
    grid: gpd.GeoDataFrame,
    graph: nx.MultiDiGraph,
    max_distance: float | None = None,
    projected: bool = False,
) -> gpd.GeoDataFrame:
    """
    Vincula os centróides da malha territorial aos nós mais
    próximos da rede viária.

    Para cada célula da malha, encontra o nó do grafo mais
    próximo do seu centróide. O resultado é um GeoDataFrame
    com as colunas:

    - ``node_id`` : identificador do nó no grafo
    - ``node_geometry`` : ``Point`` — coordenadas do nó
    - ``snap_distance`` : distância (m) entre centróide e nó
    - ``grid_centroid`` : ``Point`` — centróide da célula original

    Parameters
    ----------
    grid : geopandas.GeoDataFrame
        Malha territorial (saída de :func:`ambx.grid.generate_grid`),
        em EPSG:4326.
    graph : networkx.MultiDiGraph
        Grafo viário, de preferência já projetado (UTM).
    max_distance : float | None, default None
        Distância máxima (metros) para vinculação. Células cujo
        centróide está além dessa distância de qualquer nó são
        descartadas. Se ``None``, não há limite.
    projected : bool, default False
        Indica se ``grid`` e ``graph`` já estão no mesmo CRS métrico.
        Se ``False``, as projeções são feitas automaticamente.

    Returns
    -------
    geopandas.GeoDataFrame
        GeoDataFrame com uma linha por célula da malha vinculada
        a um nó do grafo. CRS métrico (UTM).
    """
    # --- Projeção para CRS métrico comum, se necessário ---
    if not projected:
        # Projeta o grid
        grid_utm = grid.to_crs(utm_crs(grid.geometry.union_all()))
        # Projeta o grafo se ainda estiver em 4326
        nodes_gdf = ox.graph_to_gdfs(graph, nodes=True, edges=False)
        if nodes_gdf.crs is None or nodes_gdf.crs.is_geographic:
            graph = project_network(graph)
        grid = grid_utm

    # --- Extrair nós do grafo como GeoDataFrame ---
    nodes = ox.graph_to_gdfs(graph, nodes=True, edges=False)
    # Garantir que temos uma coluna de geometria chamada 'geometry'
    if "geometry" not in nodes.columns:
        nodes = nodes.set_geometry(
            gpd.points_from_xy(nodes["x"], nodes["y"]),
            crs=graph.graph.get("crs", nodes.crs),
        )
    # Garantir que osmid seja uma coluna (em versões recentes do osmnx,
    # o osmid é o índice do GeoDataFrame, não uma coluna)
    if "osmid" not in nodes.columns:
        nodes = nodes.reset_index()  # move o índice (osmid) para coluna

    # --- Centróides da malha ---
    centroids = grid.geometry.centroid
    centroid_gdf = gpd.GeoDataFrame(
        geometry=centroids,
        crs=grid.crs,
    )
    centroid_gdf["cell_idx"] = centroid_gdf.index

    # --- Nearest join: para cada centróide, encontra o nó mais próximo ---
    # sjoin_nearest faz spatial join pelo vizinho mais próximo
    snapped = gpd.sjoin_nearest(
        centroid_gdf,
        nodes[["geometry", "osmid"]],
        how="left",
        distance_col="snap_distance",
    )

    # Renomeia colunas para clareza
    snapped = snapped.rename(columns={
        "osmid": "node_id",
        "geometry": "grid_centroid",
    })

    # Adiciona geometria do nó vinculado
    node_geom_map = dict(zip(nodes["osmid"], nodes.geometry))
    snapped["node_geometry"] = snapped["node_id"].map(node_geom_map)
    snapped["node_geometry"] = snapped["node_geometry"].apply(
        lambda g: g if g is not None else None
    )

    # --- Filtro de distância máxima ---
    if max_distance is not None:
        snapped = snapped[snapped["snap_distance"] <= max_distance]

    # Remove células não vinculadas (node_id nulo)
    snapped = snapped.dropna(subset=["node_id"])

    # Converte node_id para tipo nativo (evita numpy int)
    snapped["node_id"] = snapped["node_id"].astype(int)

    # A geometria final é o ponto do nó
    result = gpd.GeoDataFrame(
        snapped.drop(columns=["grid_centroid"]),
        geometry="node_geometry",
        crs=nodes.crs,
    )

    return result


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def get_graph_nodes(
    graph: nx.MultiDiGraph,
) -> gpd.GeoDataFrame:
    """
    Retorna os nós do grafo como GeoDataFrame.

    Atalho para ``ox.graph_to_gdfs(graph, nodes=True, edges=False)``.

    Parameters
    ----------
    graph : networkx.MultiDiGraph
        Grafo viário.

    Returns
    -------
    geopandas.GeoDataFrame
        Nós do grafo com geometria ``Point``.
    """
    return ox.graph_to_gdfs(graph, nodes=True, edges=False)


def get_graph_edges(
    graph: nx.MultiDiGraph,
) -> gpd.GeoDataFrame:
    """
    Retorna as arestas do grafo como GeoDataFrame.

    Atalho para ``ox.graph_to_gdfs(graph, nodes=False, edges=True)``.

    Parameters
    ----------
    graph : networkx.MultiDiGraph
        Grafo viário.

    Returns
    -------
    geopandas.GeoDataFrame
        Arestas do grafo com geometria ``LineString``.
    """
    return ox.graph_to_gdfs(graph, nodes=False, edges=True)


def add_travel_time(
    graph: nx.MultiDiGraph,
    speed_kph: float = 5.0,
) -> nx.MultiDiGraph:
    """
    Adiciona o atributo ``travel_time`` (em minutos) às arestas do
    grafo com base na velocidade de deslocamento.

    Parameters
    ----------
    graph : networkx.MultiDiGraph
        Grafo viário com arestas contendo o atributo ``length`` (em metros).
    speed_kph : float, default 5.0
        Velocidade de deslocamento em km/h.
        Valores típicos:
        - Caminhada: 5 km/h
        - Bicicleta: 15 km/h

    Returns
    -------
    networkx.MultiDiGraph
        Grafo com o atributo ``travel_time`` (minutos) em cada aresta.
    """
    speed_mpm = speed_kph * 1000 / 60  # metros por minuto

    for u, v, k, data in graph.edges(data=True, keys=True):
        length = data.get("length", 0)
        data["travel_time"] = length / speed_mpm

    return graph
