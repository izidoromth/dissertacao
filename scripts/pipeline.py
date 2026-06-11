"""
pipeline.py — Entry point do framework ambx.

Orquestra o fluxo completo: modelagem territorial, cenários,
cálculo de indicadores, análise comparativa e de desigualdade.
"""

from ambx.grid import generate_grid, GridFormat
from ambx.pois import get_pois

# ---------------------------------------------------------------------------
# Parâmetros globais (futuramente via CLI ou arquivo de configuração)
# ---------------------------------------------------------------------------
LOCATION = "Compiegne, Hauts de France, France"
GRID_FORMAT = GridFormat.HEXAGON
CELL_SIZE = 500  # metros
POI_BUFFER = 2000  # metros — captura serviços de cidades vizinhas


def main() -> None:
    """Executa o pipeline completo de análise de acessibilidade."""
    # ------------------------------------------------------------------
    # Stage 1a: Malha territorial
    # ------------------------------------------------------------------
    print(f"[1/3] Gerando malha para: {LOCATION}")
    grid = generate_grid(LOCATION, grid_format=GRID_FORMAT, cell_size=CELL_SIZE)
    print(f"       {len(grid)} células ({GRID_FORMAT.value}, {CELL_SIZE}m)")

    # ------------------------------------------------------------------
    # Stage 1b: Pontos de Interesse
    # ------------------------------------------------------------------
    print(f"[2/3] Coletando POIs (buffer={POI_BUFFER}m)...")
    pois = get_pois(LOCATION, buffer=POI_BUFFER)
    cats = pois["category"].value_counts()
    for cat, count in cats.items():
        print(f"       {cat}: {count}")

    # ------------------------------------------------------------------
    # Stage 1c: Rede viária (TODO)
    # ------------------------------------------------------------------
    print(f"[3/3] Rede viária: (a implementar)")

    # TODO: próximas etapas do pipeline


if __name__ == "__main__":
    main()