"""
ambx — Análise de Acessibilidade (Streamlit)
=============================================
Prototipo interativo com cache para explorar malha, rede viaria,
POIs e snapping sem sofrer com a lentidao do notebook + folium.

Executar:
    streamlit run streamlit_app.py
"""

import sys
from pathlib import Path

# Adiciona scripts/ ao path
sys.path.insert(0, str(Path(__file__).resolve().parent / "../scripts"))

import folium
import multiprocessing as mp
import streamlit as st
from streamlit_folium import st_folium

# Garante que mp.Pool use fork (Linux) — evita deadlock com spawn no Streamlit
try:
    mp.set_start_method("fork", force=True)
except RuntimeError:
    pass  # já setado ou plataforma sem fork (macOS/Windows)

from ambx.grid import generate_grid, GridFormat
from ambx.network import (
    add_travel_time,
    get_graph_edges,
    get_network,
    project_network,
    snap_grid_to_network,
)
from ambx.pois import get_pois
from ambx.routing import snap_pois_to_network, routing_matrix

# ---------------------------------------------------------------------------
# Configuração da página
# ---------------------------------------------------------------------------
st.set_page_config(
    page_title="ambx — Acessibilidade",
    page_icon="🗺️",
    layout="wide",
)

st.title("🗺️ ambx — Análise de Acessibilidade Territorial")
st.caption("Protótipo interativo — altere os parâmetros na barra lateral")

# ---------------------------------------------------------------------------
# Sidebar — Parâmetros
# ---------------------------------------------------------------------------
with st.sidebar:
    st.header("⚙️ Parâmetros")

    st.subheader("🔍 Modo de Visualização")
    VIEW_MODE = st.radio(
        "Selecione a visão",
        options=["Preparação de Dados", "Tempos Médios (A*)"],
        index=0,
    )

    st.divider()

    LOCATION = st.text_input(
        "Localidade",
        value="Curitiba, Parana, Brazil",
        help="Nome da cidade conforme OpenStreetMap",
    )

    GRID_OPTIONS = {"Hexágono": GridFormat.HEXAGON, "Quadrado": GridFormat.SQUARE}
    grid_label = st.selectbox("Formato da malha", list(GRID_OPTIONS.keys()), index=0)
    GRID_FORMAT = GRID_OPTIONS[grid_label]

    CELL_SIZE = st.slider(
        "Tamanho da célula (m)",
        min_value=100,
        max_value=1000,
        value=200,
        step=50,
        help="Raio do hexágono ou lado do quadrado",
    )

    POI_BUFFER = st.slider(
        "Buffer POIs (m)",
        min_value=500,
        max_value=5000,
        value=2000,
        step=500,
        help="Margem além do contorno para capturar serviços vizinhos",
    )

    NETWORK_TYPE = st.selectbox(
        "Tipo de rede",
        options=["walk", "bike", "drive"],
        index=0,
        format_func=lambda x: {"walk": "A pé", "bike": "Bicicleta", "drive": "Carro"}[x],
    )

    WALK_SPEED_KPH = st.slider(
        "Velocidade (km/h)",
        min_value=1.0,
        max_value=60.0,
        value=5.0,
        step=0.5,
    )

    MAX_SNAP_DIST = st.slider(
        "Distância máx. snapping (m)",
        min_value=200,
        max_value=3000,
        value=1000,
        step=100,
        help="Células com centróide mais distante que isso de qualquer nó são descartadas",
    )

    if VIEW_MODE == "Preparação de Dados":
        EDGES_SAMPLE = st.slider(
            "Amostra de arestas no mapa",
            min_value=1000,
            max_value=20000,
            value=5000,
            step=1000,
            help="Quantas arestas renderizar (menos = mais rápido)",
        )
    else:
        K_NEAREST = st.slider(
            "K vizinhos por categoria",
            min_value=1,
            max_value=10,
            value=3,
            step=1,
            help="Quantos POIs mais próximos considerar por categoria",
        )
        N_JOBS = st.slider(
            "Nº de processos paralelos",
            min_value=1,
            max_value=38,
            value=min(8, 38),
            step=1,
            help="Núcleos para paralelismo (mais = mais rápido, mas usa mais RAM)",
        )

    st.divider()
    st.caption("Dados: © OpenStreetMap contributors")

# ---------------------------------------------------------------------------
# Cache — operações pesadas
# ---------------------------------------------------------------------------

@st.cache_data(show_spinner=False)
def cached_network(location: str, network_type: str, speed_kph: float):
    """Baixa e prepara o grafo viário (cacheado por localidade + tipo)."""
    with st.spinner(f"Baixando grafo viário para {location}..."):
        graph = get_network(location, network_type=network_type)
        graph = project_network(graph)
        graph = add_travel_time(graph, speed_kph=speed_kph)
    return graph


@st.cache_data(show_spinner=False)
def cached_grid(location: str, grid_format: GridFormat, cell_size: int):
    """Gera a malha territorial (cacheada)."""
    with st.spinner("Gerando malha territorial..."):
        grid = generate_grid(location, grid_format=grid_format, cell_size=cell_size)
    return grid


@st.cache_data(show_spinner=False)
def cached_pois(location: str, buffer: int):
    """Coleta POIs (cacheado)."""
    with st.spinner("Coletando pontos de interesse..."):
        pois = get_pois(location, buffer=buffer)
    return pois


@st.cache_data(show_spinner=False)
def cached_snapped(grid_wkb: bytes, graph_wkb, max_distance: int):
    """
    Faz o snapping malha ↔ rede (cacheado).
    Recebe WKB pq GeoDataFrame/MultiDiGraph não são hashaveis diretamente.
    """
    import pickle
    grid = pickle.loads(grid_wkb)
    graph = pickle.loads(graph_wkb)
    with st.spinner("Vinculando malha à rede (snapping)..."):
        snapped = snap_grid_to_network(grid, graph, projected=False, max_distance=max_distance)
    return snapped


@st.cache_data(show_spinner=False)
def cached_pois_snapped(pois_wkb: bytes, graph_wkb):
    """Snap dos POIs à rede (cacheado)."""
    import pickle
    pois = pickle.loads(pois_wkb)
    graph = pickle.loads(graph_wkb)
    with st.spinner("Vinculando POIs à rede..."):
        pois_snapped = snap_pois_to_network(pois, graph)
    return pois_snapped


@st.cache_data(show_spinner=False)
def cached_routing_matrix(
    snapped_wkb: bytes,
    pois_snapped_wkb: bytes,
    graph_wkb,
    k_nearest: int,
    weight: str,
    speed_kph: float,
    n_jobs: int,
):
    """Calcula a matriz origem-destino A* (cacheado)."""
    import pickle
    snapped = pickle.loads(snapped_wkb)
    pois_snapped = pickle.loads(pois_snapped_wkb)
    graph = pickle.loads(graph_wkb)
    matrix = routing_matrix(
        snapped, pois_snapped, graph,
        k_nearest=k_nearest,
        weight=weight,
        speed_kph=speed_kph,
        n_jobs=n_jobs,
    )
    return matrix


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------
import pickle

grid = cached_grid(LOCATION, GRID_FORMAT, CELL_SIZE)
graph = cached_network(LOCATION, NETWORK_TYPE, WALK_SPEED_KPH)
snapped = cached_snapped(pickle.dumps(grid), pickle.dumps(graph), MAX_SNAP_DIST)
pois = cached_pois(LOCATION, POI_BUFFER)

# Paleta de cores dos POIs (usada em ambas as visões)
colors_map = {
    "health": "#e31a1c",
    "education": "#33a02c",
    "transportation": "#ff7f00",
    "food": "#6a3d9a",
}

if VIEW_MODE == "Preparação de Dados":
    # --- Métricas ---
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Células", len(grid))
    col2.metric("Nós do grafo", graph.number_of_nodes())
    col3.metric("Arestas do grafo", graph.number_of_edges())
    col4.metric("Células vinculadas", len(snapped))

    # --- Estatísticas ---
    with st.expander("📊 Estatísticas", expanded=False):
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Taxa de vinculação", f"{len(snapped) / len(grid) * 100:.1f}%")
        c2.metric("Distância média snap", f"{snapped['snap_distance'].mean():.1f} m")
        c3.metric("Distância máx. snap", f"{snapped['snap_distance'].max():.1f} m")
        c4.metric("Total de POIs", len(pois))
        st.write("**POIs por categoria:**")
        st.dataframe(
            pois["category"].value_counts().rename_axis("Categoria").reset_index(name="Quantidade"),
            use_container_width=True,
        )

    # --- Mapa: camadas de preparação ---
    st.subheader("📍 Mapa Interativo — Preparação de Dados")

    centroid = grid.geometry.union_all().centroid
    m = folium.Map(location=[centroid.y, centroid.x], zoom_start=13, control_scale=True)

    grid.explore(
        m=m,
        name="Malha Territorial",
        style_kwds={
            "fillColor": "#1f77b4",
            "color": "#0d3b66",
            "weight": 0.8,
            "fillOpacity": 0.10,
        },
    )

    edges = get_graph_edges(graph).to_crs(epsg=4326)
    n_edges = len(edges)
    edges_sample = (
        edges.sample(min(EDGES_SAMPLE, n_edges), random_state=42)
        if n_edges > EDGES_SAMPLE
        else edges
    )
    edges_sample.explore(
        m=m,
        name=f"Rede Viária ({len(edges_sample)} arestas)",
        style_kwds={"color": "#2ca02c", "weight": 2.5},
    )

    snapped.explore(
        m=m,
        name=f"Nós Snapped ({len(snapped)})",
        color="#8f8f8f",
        marker_kwds={"radius": 2.5},
    )

    for cat, color in colors_map.items():
        subset = pois[pois["category"] == cat]
        if subset.empty:
            continue
        subset.explore(
            m=m,
            name=f"POI — {cat} ({len(subset)})",
            color=color,
            marker_kwds={"radius": 3},
        )

    folium.LayerControl().add_to(m)
    st_folium(m, height=650, width=None, returned_objects=[])

else:
    # --- Modo: Tempos Médios (A*) ---
    import pandas as pd

    # Snap dos POIs
    pois_snapped = cached_pois_snapped(pickle.dumps(pois), pickle.dumps(graph))

    # Estimativa para feedback
    cats = pois_snapped["category"].nunique()
    est_pairs = len(snapped) * cats * K_NEAREST
    st.info(
        f"Calculando A*: **{len(snapped)} células** × "
        f"**{cats} categorias** × **{K_NEAREST} vizinhos** = "
        f"**{est_pairs} pares** com **{N_JOBS} workers**..."
    )

    # Matriz OD (todas as células vinculadas)
    with st.status("Calculando caminhos mínimos com A*...", expanded=True) as status:
        matrix = cached_routing_matrix(
            pickle.dumps(snapped),
            pickle.dumps(pois_snapped),
            pickle.dumps(graph),
            K_NEAREST,
            "travel_time",
            WALK_SPEED_KPH,
            N_JOBS,
        )
        status.update(label=f"✓ {len(matrix)} pares calculados!", state="complete")

    # --- Métricas ---
    n_pairs = len(matrix)
    reachable = matrix["travel_time"].notna().sum()
    unreachable = matrix["travel_time"].isna().sum()
    tt = matrix.loc[matrix["travel_time"].notna(), "travel_time"]

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Células vinculadas", len(snapped))
    col2.metric("Pares calculados", n_pairs)
    col3.metric("Alcançáveis", f"{reachable} ({reachable / n_pairs * 100:.0f}%)")
    col4.metric("Tempo médio A*", f"{tt.mean():.1f} min" if len(tt) > 0 else "N/A")

    # --- Estatísticas ---
    with st.expander("📊 Estatísticas dos Tempos de Viagem", expanded=False):
        if len(tt) > 0:
            c1, c2, c3, c4 = st.columns(4)
            c1.metric("Mediana", f"{tt.median():.1f} min")
            c2.metric("Mínimo", f"{tt.min():.1f} min")
            c3.metric("Máximo", f"{tt.max():.1f} min")
            c4.metric("Desvio padrão", f"{tt.std():.1f} min")

            st.write("**Tempo médio por categoria:**")
            avg_by_cat = (
                matrix.groupby("poi_category")["travel_time"]
                .mean()
                .dropna()
                .sort_values()
            )
            st.dataframe(
                (avg_by_cat).rename("Tempo médio (min)").reset_index(),
                use_container_width=True,
            )

            corr = matrix["euclidean_dist"].corr(matrix["travel_time"])
            st.metric("Correlação euclidiana × A*", f"{corr:.3f}")
        else:
            st.warning("Nenhum par alcançável — verifique a conectividade da rede.")

    # --- Mapa: células coloridas por tempo médio ---
    st.subheader("📍 Mapa Interativo — Tempo Médio de Acesso (A*)")

    avg_travel_time = (
        matrix.groupby("cell_idx")["travel_time"]
        .mean()
        .reset_index()
        .rename(columns={"travel_time": "avg_travel_time"})
    )

    cells_gdf = grid.loc[snapped.index].copy()
    cells_gdf["cell_idx"] = cells_gdf.index
    cells_plot = cells_gdf.merge(avg_travel_time, on="cell_idx", how="left")

    # Cria faixas discretas manualmente (evita dependência de mapclassify)
    bins = [0, 5, 10, 15, 20, 25, 30, 45, 60, float("inf")]
    labels = ["0–5 min", "5–10 min", "10–15 min", "15–20 min", "20–25 min", "25–30 min", "30–45 min", "45–60 min", ">60 min"]
    cells_plot["faixa"] = pd.cut(
        cells_plot["avg_travel_time"], bins=bins, labels=labels, right=False
    )

    m2 = cells_plot.explore(
        column="faixa",
        cmap="magma_r",
        legend=True,
        legend_kwds={"caption": "Tempo médio de acesso (min)", "color": "#212121"},
        tooltip=["cell_idx", "avg_travel_time"],
        style_kwds={"weight": 1.0, "fillOpacity": 0.75},
    )

    # Adiciona POIs como referência
    for cat, color in colors_map.items():
        subset = pois[pois["category"] == cat]
        if subset.empty:
            continue
        subset.explore(
            m=m2,
            name=f"POI — {cat} ({len(subset)})",
            color=color,
            marker_kwds={"radius": 3},
        )

    folium.LayerControl().add_to(m2)
    st_folium(m2, height=650, width=None, returned_objects=[])

# ---------------------------------------------------------------------------
# Rodapé
# ---------------------------------------------------------------------------
st.divider()
st.caption(
    "Próximas etapas: environment → routing (Dijkstra) → penalties → indicadores (PTh, Índice G, F15)"
)
