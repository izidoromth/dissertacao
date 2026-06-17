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
sys.path.insert(0, str(Path(__file__).resolve().parent / "scripts"))

import folium
import streamlit as st
from streamlit_folium import st_folium

from ambx.grid import generate_grid, GridFormat
from ambx.network import (
    add_travel_time,
    get_graph_edges,
    get_network,
    project_network,
    snap_grid_to_network,
)
from ambx.pois import get_pois

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

    LOCATION = st.text_input(
        "Localidade",
        value="Porto Alegre, Rio Grande do Sul, Brazil",
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

    EDGES_SAMPLE = st.slider(
        "Amostra de arestas no mapa",
        min_value=1000,
        max_value=20000,
        value=5000,
        step=1000,
        help="Quantas arestas renderizar (menos = mais rápido)",
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


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------
import pickle

col1, col2, col3, col4 = st.columns(4)

# 1a — Malha
grid = cached_grid(LOCATION, GRID_FORMAT, CELL_SIZE)
col1.metric("Células", len(grid))

# 1c — Rede
graph = cached_network(LOCATION, NETWORK_TYPE, WALK_SPEED_KPH)
col2.metric("Nós do grafo", graph.number_of_nodes())
col3.metric("Arestas do grafo", graph.number_of_edges())

# 1d — Snapping
snapped = cached_snapped(pickle.dumps(grid), pickle.dumps(graph), MAX_SNAP_DIST)
col4.metric("Células vinculadas", len(snapped))

# 1b — POIs
pois = cached_pois(LOCATION, POI_BUFFER)

# ---------------------------------------------------------------------------
# Estatísticas rápidas
# ---------------------------------------------------------------------------
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

# ---------------------------------------------------------------------------
# Mapa
# ---------------------------------------------------------------------------
st.subheader("📍 Mapa Interativo")

centroid = grid.geometry.union_all().centroid
m = folium.Map(location=[centroid.y, centroid.x], zoom_start=13, control_scale=True)

# Camada 1 — Malha Territorial
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

# Camada 2 — Rede Viária (amostra)
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

# Camada 3 — Nós Snapped
snapped.explore(
    m=m,
    name=f"Nós Snapped ({len(snapped)})",
    color="#8f8f8f",
    marker_kwds={"radius": 2.5},
)

# Camada 4 — POIs
colors_map = {
    "health":         "#e31a1c",
    "education":      "#33a02c",
    "transportation": "#ff7f00",
    "food":           "#6a3d9a",
}
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

# Renderiza com st_folium (bem mais leve que output inline do notebook)
st_folium(m, height=650, width=None, returned_objects=[])

# ---------------------------------------------------------------------------
# Rodapé
# ---------------------------------------------------------------------------
st.divider()
st.caption(
    "Próximas etapas: environment → routing (Dijkstra) → penalties → indicadores (PTh, Índice G, F15)"
)
