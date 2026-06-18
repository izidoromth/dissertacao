"""
Módulo de roteamento A* sobre a rede viária.

Calcula os caminhos mínimos entre origens (células da malha)
e destinos (POIs) utilizando o algoritmo A* com heurística
de distância euclidiana (admissível e consistente para grafos
espaciais projetados em CRS métrico).
"""

from __future__ import annotations

import geopandas as gpd
import networkx as nx
import numpy as np
import pandas as pd
from math import sqrt
from typing import Callable


# ---------------------------------------------------------------------------
# Globais para paralelismo (multiprocessing.Pool com initializer)
# ---------------------------------------------------------------------------
_graph: nx.MultiDiGraph | None = None
_weight: str = "travel_time"
_speed_kph: float = 5.0


def _init_worker(graph, weight, speed_kph):
    """
    Initializer do multiprocessing.Pool.

    Seta os globais ``_graph``, ``_weight`` e ``_speed_kph``
    em cada worker. Com *fork* os dados já estão em memória
    (copy-on-write); com *spawn* o grafo é serializado uma
    única vez por worker via ``initargs``.
    """
    global _graph, _weight, _speed_kph
    _graph = graph
    _weight = weight
    _speed_kph = speed_kph


def _astar_task(args: tuple) -> tuple[int, float | None]:
    """
    Task module-level para multiprocessing.Pool.imap_unordered.

    Recebe ``(idx, src, tgt)`` e usa os globais ``_graph``,
    ``_weight`` e ``_speed_kph`` setados por :func:`_init_worker`.
    """
    idx, src, tgt = args
    return idx, astar_shortest_path(_graph, src, tgt, _weight, _speed_kph)


# ---------------------------------------------------------------------------
# Heurística A* — distância euclidiana / velocidade
# ---------------------------------------------------------------------------
def _build_heuristic(
    graph: nx.MultiDiGraph,
    target: int,
    speed_kph: float = 5.0,
) -> Callable[[int], float]:
    """
    Constrói a função heurística ``h(u)`` para o A*.

    Estima o tempo restante do nó *u* até *target* como:
    ``distância_euclidiana(u, target) / speed_mpm``,
    onde *speed_mpm* é a velocidade em metros por minuto.

    A heurística é **admissível** porque a distância em linha
    reta nunca excede a distância real pela rede. Também é
    **consistente** pela desigualdade triangular da métrica
    euclidiana.

    Parameters
    ----------
    graph : networkx.MultiDiGraph
        Grafo com nós contendo atributos ``x`` e ``y``
        (coordenadas UTM em metros).
    target : int
        ID do nó de destino.
    speed_kph : float, default 5.0
        Velocidade de deslocamento (km/h). Padrão: 5 km/h.

    Returns
    -------
    Callable[[int], float]
        Função ``h(u)`` que retorna o tempo estimado (minutos).
    """
    speed_mpm = speed_kph * 1000 / 60  # km/h → m/min
    nodes = graph._node
    tx, ty = nodes[target]["x"], nodes[target]["y"]

    def h(u: int, _target: int = target) -> float:
        ux, uy = nodes[u]["x"], nodes[u]["y"]
        dx, dy = ux - tx, uy - ty
        return sqrt(dx * dx + dy * dy) / speed_mpm

    return h


# ---------------------------------------------------------------------------
# Snap dos POIs à rede
# ---------------------------------------------------------------------------
def snap_pois_to_network(
    pois: gpd.GeoDataFrame,
    graph: nx.MultiDiGraph,
) -> gpd.GeoDataFrame:
    """
    Vincula cada POI ao nó mais próximo da rede viária.

    Parameters
    ----------
    pois : geopandas.GeoDataFrame
        POIs categorizados, em EPSG:4326.
    graph : networkx.MultiDiGraph
        Grafo viário projetado (UTM).

    Returns
    -------
    geopandas.GeoDataFrame
        Cópia de *pois* com colunas adicionais:
        ``node_id`` (nó do grafo) e ``snap_distance`` (metros).
        CRS métrico (UTM).
    """
    # Projeta POIs para o CRS do grafo
    graph_crs = graph.graph.get("crs", None)
    pois_utm = pois.to_crs(graph_crs) if graph_crs else pois

    nodes = ox_graph_to_nodes_gdf(graph)

    centroids = gpd.GeoDataFrame(
        geometry=pois_utm.geometry,
        crs=pois_utm.crs,
    )
    centroids["poi_idx"] = centroids.index

    snapped = gpd.sjoin_nearest(
        centroids,
        nodes[["geometry", "osmid"]],
        how="left",
        distance_col="snap_distance",
    )

    snapped = snapped.rename(columns={"osmid": "node_id"})
    snapped["node_id"] = snapped["node_id"].astype(int)

    # Junta de volta com os atributos originais dos POIs
    result = pois_utm.copy()
    result["node_id"] = snapped["node_id"].values
    result["snap_distance"] = snapped["snap_distance"].values

    result = result.dropna(subset=["node_id"])
    result["node_id"] = result["node_id"].astype(int)

    return gpd.GeoDataFrame(result, geometry="geometry", crs=pois_utm.crs)


# ---------------------------------------------------------------------------
# A* entre dois nós
# ---------------------------------------------------------------------------
def astar_shortest_path(
    graph: nx.MultiDiGraph,
    source: int,
    target: int,
    weight: str = "travel_time",
    speed_kph: float = 5.0,
) -> float | None:
    """
    Calcula o caminho mínimo entre dois nós usando A*.

    Parameters
    ----------
    graph : networkx.MultiDiGraph
        Grafo viário projetado (UTM), com arestas contendo
        o atributo *weight*.
    source : int
        ID do nó de origem.
    target : int
        ID do nó de destino.
    weight : str, default "travel_time"
        Atributo da aresta usado como custo.
    speed_kph : float, default 5.0
        Velocidade de deslocamento (km/h) para a heurística.

    Returns
    -------
    float | None
        Tempo total (minutos) do caminho mínimo, ou ``None``
        se não houver rota.
    """
    try:
        heuristic = _build_heuristic(graph, target, speed_kph)
        length = nx.astar_path_length(
            graph, source, target, heuristic=heuristic, weight=weight
        )
        return length
    except (nx.NetworkXNoPath, nx.NodeNotFound):
        return None


# ---------------------------------------------------------------------------
# Matriz origem-destino completa
# ---------------------------------------------------------------------------
def routing_matrix(
    snapped: gpd.GeoDataFrame,
    pois_snapped: gpd.GeoDataFrame,
    graph: nx.MultiDiGraph,
    k_nearest: int = 3,
    weight: str = "travel_time",
    speed_kph: float = 5.0,
    n_jobs: int = 1,
) -> pd.DataFrame:
    """
    Calcula a matriz origem-destino para os *K* POIs mais
    próximos de cada categoria, para cada célula.

    Para cada célula da malha, seleciona os *K* POIs mais
    próximos **por categoria** com base na distância euclidiana
    entre os nós vinculados da rede. Só então executa A* nesses
    pares filtrados.

    Suporta paralelismo via ``joblib`` (recomendado: ``n_jobs=-1``
    para usar todos os núcleos).

    Parameters
    ----------
    snapped : geopandas.GeoDataFrame
        Saída de :func:`snap_grid_to_network`. Colunas esperadas:
        ``cell_idx``, ``node_id``, geometria ativa (UTM).
    pois_snapped : geopandas.GeoDataFrame
        POIs vinculados à rede (saída de :func:`snap_pois_to_network`).
        Colunas esperadas: ``node_id``, ``category``, ``name``,
        geometria ativa (UTM).
    graph : networkx.MultiDiGraph
        Grafo viário projetado (UTM).
    k_nearest : int, default 3
        Quantos POIs mais próximos considerar **por categoria**.
    weight : str, default "travel_time"
        Atributo da aresta usado como custo.
    speed_kph : float, default 5.0
        Velocidade (km/h) para a heurística A*.
    n_jobs : int, default 1
        Número de processos paralelos. Use ``-1`` para todos os núcleos.
        Requer ``joblib`` instalado. Se ``1``, executa sequencial.

    Returns
    -------
    pandas.DataFrame
        DataFrame com colunas:

        - ``cell_idx`` : índice da célula de origem
        - ``poi_idx`` : índice do POI de destino
        - ``poi_name`` : nome do POI
        - ``poi_category`` : categoria do POI
        - ``euclidean_dist`` : distância euclidiana (m) entre os nós
        - ``travel_time`` : tempo mínimo (min) via A*; ``NaN`` se inalcançável
    """
    nodes = graph._node

    # --- Mapeamento node_id → (x, y) para cálculo rápido de distância ---
    node_xy = {nid: (data["x"], data["y"]) for nid, data in nodes.items()}

    # --- Origens: cell_idx, node_id, (x, y) ---
    origins = snapped[["cell_idx", "node_id"]].drop_duplicates().copy()
    origins["ox"] = origins["node_id"].map(lambda n: node_xy.get(n, (np.nan, np.nan))).apply(lambda t: t[0])
    origins["oy"] = origins["node_id"].map(lambda n: node_xy.get(n, (np.nan, np.nan))).apply(lambda t: t[1])
    origins = origins.dropna(subset=["ox", "oy"])

    # --- Destinos: poi_idx, node_id, category, name, (x, y) ---
    dests = pois_snapped[["node_id", "category", "name"]].reset_index(drop=True)
    dests.index.name = "poi_idx"
    dests = dests.reset_index()
    dests["dx"] = dests["node_id"].map(lambda n: node_xy.get(n, (np.nan, np.nan))).apply(lambda t: t[0])
    dests["dy"] = dests["node_id"].map(lambda n: node_xy.get(n, (np.nan, np.nan))).apply(lambda t: t[1])
    dests = dests.dropna(subset=["dx", "dy"])

    categories = dests["category"].unique()

    # --- Fase 1: monta lista de pares (src, tgt) ---
    pairs = []  # cada entrada: (src, tgt, cell_idx, poi_idx, euclidean)
    total_cells = len(origins)
    for i, (_, orig) in enumerate(origins.iterrows()):
        cell = orig["cell_idx"]
        src = orig["node_id"]
        ox, oy = orig["ox"], orig["oy"]

        for cat in categories:
            cat_dests = dests[dests["category"] == cat].copy()
            if cat_dests.empty:
                continue
            cat_dests["euclidean"] = np.sqrt(
                (cat_dests["dx"] - ox) ** 2 + (cat_dests["dy"] - oy) ** 2
            )
            top_k = cat_dests.nsmallest(k_nearest, "euclidean")
            for _, dest in top_k.iterrows():
                pairs.append((
                    src, dest["node_id"],
                    cell, dest["poi_idx"], dest["euclidean"],
                ))

        if (i + 1) % 50 == 0 or i == 0:
            print(f"  [seleção] célula {i + 1}/{total_cells}, "
                  f"{len(pairs)} pares acumulados", flush=True)

    print(f"\n  Total de pares: {len(pairs)}")

    # --- Fase 2: A* (paralelo ou sequencial) ---
    if n_jobs != 1:
        import multiprocessing as mp

        # Empacota tarefas como (idx, src, tgt) para imap_unordered
        tasks = [(i, src, tgt) for i, (src, tgt, *_) in enumerate(pairs)]
        chunksize = max(1, len(tasks) // (n_jobs * 4))  # ~4 chunks por worker

        print(f"  Executando A* em paralelo (n_jobs={n_jobs}, "
              f"chunksize={chunksize})...", flush=True)

        with mp.Pool(
            processes=n_jobs,
            initializer=_init_worker,
            initargs=(graph, weight, speed_kph),
        ) as pool:
            travel_times = [None] * len(tasks)
            done = 0
            for idx, tt in pool.imap_unordered(
                _astar_task, tasks, chunksize=chunksize
            ):
                travel_times[idx] = tt
                done += 1
                if done % max(1, len(tasks) // 100) == 0 or done == len(tasks):
                    print(f"  [A*] {done}/{len(tasks)} pares concluídos", flush=True)
    else:
        travel_times = []
        for i, (src, tgt, *_) in enumerate(pairs, 1):
            travel_times.append(
                astar_shortest_path(graph, src, tgt, weight, speed_kph)
            )
            if i % 500 == 0 or i == len(pairs):
                print(f"  [A*] {i}/{len(pairs)} pares concluídos", flush=True)

    # --- Fase 3: monta DataFrame ---
    rows = []
    for (src, tgt, cell, poi, eucl), tt in zip(pairs, travel_times):
        rows.append({
            "cell_idx": cell,
            "poi_idx": poi,
            "euclidean_dist": eucl,
            "travel_time": tt,
        })

    matrix = pd.DataFrame(rows)

    # Junta metadados dos POIs
    poi_meta = dests[["poi_idx", "category", "name"]].drop_duplicates()
    matrix = matrix.merge(poi_meta, on="poi_idx", how="left")
    matrix = matrix.rename(columns={
        "category": "poi_category",
        "name": "poi_name",
    })

    return matrix


# ---------------------------------------------------------------------------
# Helpers internos
# ---------------------------------------------------------------------------
def ox_graph_to_nodes_gdf(graph: nx.MultiDiGraph) -> gpd.GeoDataFrame:
    """
    Extrai nós do grafo como GeoDataFrame, garantindo colunas
    ``osmid`` e ``geometry``.
    """
    import osmnx as ox
    nodes = ox.graph_to_gdfs(graph, nodes=True, edges=False)
    if "osmid" not in nodes.columns:
        nodes = nodes.reset_index()
    if "geometry" not in nodes.columns:
        nodes = nodes.set_geometry(
            gpd.points_from_xy(nodes["x"], nodes["y"]),
            crs=graph.graph.get("crs", nodes.crs),
        )
    return nodes
