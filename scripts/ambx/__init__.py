"""
ambx — Framework para Avaliação da Acessibilidade Urbana de Curta Distância
sob Perturbações Ambientais (Ambient Access).

Módulos:
    grid        : Geração de malha territorial (hexagonal / quadrada).
    utils       : Utilitários geoespaciais (CRS UTM, geometria).
    network     : Grafo viário a partir do OpenStreetMap.
    pois        : Coleta e categorização de Pontos de Interesse.
    routing     : Roteamento A* e matriz origem-destino.
    environment : Carregamento de camadas ambientais (raster / vetorial).
    demographics: Compatibilização de dados censitários com a malha.
    penalties   : Funções de penalização ambiental sobre arestas.
    routing     : Caminhos mínimos e matriz origem-destino.
    indicators  : Cálculo de PTh, Índice G e F15.
    comparison  : Análise comparativa entre cenários.
    inequality  : Análise de desigualdade socioeconômica.
"""

__version__ = "0.1.0"
