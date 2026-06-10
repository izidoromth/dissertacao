"""
pipeline.py — Entry point do framework ambx.

Orquestra o fluxo completo: modelagem territorial, cenários,
cálculo de indicadores, análise comparativa e de desigualdade.
"""

from ambx.grid import generate_grid, GridFormat

# ---------------------------------------------------------------------------
# Parâmetros globais (futuramente via CLI ou arquivo de configuração)
# ---------------------------------------------------------------------------
LOCATION = "Curitiba, Parana, Brazil"
GRID_FORMAT = GridFormat.HEXAGON
CELL_SIZE = 500  # metros


def main() -> None:
    """Executa o pipeline completo de análise de acessibilidade."""
    print(f"Gerando malha para: {LOCATION}")
    grid = generate_grid(LOCATION, grid_format=GRID_FORMAT, cell_size=CELL_SIZE)
    grid.to_file("test.shp")
    print(f"  -> {len(grid)} células geradas (formato: {GRID_FORMAT.value})")
    print(f"  -> CRS: {grid.crs}")
    # TODO: próximas etapas do pipeline


if __name__ == "__main__":
    main()