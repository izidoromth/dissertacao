"""
Módulo de penalização ambiental sobre a rede viária.

Fornece estruturas e funções para transformar os custos das arestas
do grafo (W_base) em custos condicionados (W_cond) a partir de
camadas ambientais carregadas pelo módulo ``environment``.

A penalização é governada por funções arbitrárias fornecidas pelo
usuário (``penalty_fn``) que mapeiam o valor de uma camada para um
fator multiplicador do custo da aresta.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Literal

import geopandas as gpd

from ambx.environment import EnvironmentLayers, VectorLayer


# ---------------------------------------------------------------------------
# Estruturas de configuração
# ---------------------------------------------------------------------------


@dataclass
class PenaltyRule:
    """Regra de penalização para uma camada ambiental.

    Define **o quê**, **como** e **sobre qual campo** uma camada
    ambiental impacta os custos da rede.

    Attributes
    ----------
    layer_name : str
        Nome da camada em ``EnvironmentLayers`` (ex.: ``"inundacao_2024"``
        para vetorial, ``"temperatura_superficie"`` para raster).
    layer_type : Literal["raster", "vector"]
        Tipo da camada.
    weight_field : str
        Nome do campo de custo nas arestas a ser multiplicado
        pelo fator de penalidade (ex.: ``"travel_time"`` ou ``"length"``).
    penalty_fn : Callable[[Any], float]
        Função que recebe o valor extraído da camada na região da
        aresta e retorna o **fator multiplicador** do custo.
        O valor pode ser ``float`` (para rasters ou colunas numéricas)
        ou ``str`` (para colunas categóricas como ``"Alta"``).
        Deve retornar ``float("inf")`` para interdição total.

        Examples
        --------
        >>> def flood_factor(depth: float) -> float:
        ...     if depth > 50: return float("inf")
        ...     if depth > 20: return 3.0
        ...     if depth > 5:  return 1.5
        ...     return 1.0
        >>> PenaltyRule("inundacao", "vector", penalty_fn=flood_factor)
        PenaltyRule(layer_name='inundacao', layer_type='vector', ...)
    """

    layer_name: str
    layer_type: Literal["raster", "vector"]
    weight_field: str | None = None
    penalty_fn: Callable[[Any], float] = field(default=lambda v: 1.0)


# ---------------------------------------------------------------------------
# Penalização de camadas vetoriais
# ---------------------------------------------------------------------------


def apply_vector_penalty(
    edges_gdf: gpd.GeoDataFrame,
    vector_layer: VectorLayer,
    rule: PenaltyRule,
) -> gpd.GeoDataFrame:
    """Aplica penalidade vetorial sobre as arestas da rede.

    Para cada aresta, identifica os polígonos da camada vetorial que
    a intersectam via ``gpd.sjoin``. O valor da coluna de penalidade
    (``vector_layer.value_column``) de cada polígono é passado para
    ``rule.penalty_fn``, e o **maior fator** (pior caso) é aplicado
    ao campo de custo.

    Arestas sem interseção permanecem com o custo original (fator 1.0).

    Parameters
    ----------
    edges_gdf : gpd.GeoDataFrame
        Arestas da rede com geometria ``LineString`` e o campo de
        custo definido em ``rule.weight_field``.
    vector_layer : VectorLayer
        Camada vetorial com polígonos de penalidade.
        Deve ter ``value_column`` definido.
    rule : PenaltyRule
        Regra de penalização com ``penalty_fn``.

    Returns
    -------
    gpd.GeoDataFrame
        ``edges_gdf`` com o campo ``rule.weight_field`` atualizado.

    Raises
    ------
    ValueError
        Se ``vector_layer.value_column`` não estiver definido.
    """
    if vector_layer.value_column is None:
        raise ValueError(
            f"VectorLayer '{vector_layer.name}' não possui value_column. "
            "Defina value_column ao carregar a camada."
        )

    if vector_layer.gdf.crs != edges_gdf.crs:
        vector_layer.gdf.to_crs(edges_gdf.crs, inplace=True)

    # Interseção espacial: cada aresta pode se ligar a múltiplos polígonos
    joined = gpd.sjoin(
        edges_gdf,
        vector_layer.gdf[[vector_layer.value_column, "geometry"]],
        how="left",
        predicate="intersects",
    )

    # Para cada aresta, calcula o maior fator entre todos os polígonos
    # que a intersectam. Arestas sem interseção ficam com fator 1.0.
    val_col = vector_layer.value_column

    # Fator por aresta: máximo da penalty_fn aplicada aos valores
    # dos polígonos que intersectam
    def _max_factor(group):
        values = group[val_col].dropna()
        if len(values) == 0:
            return 1.0
        return max(rule.penalty_fn(v) for v in values)

    factors = joined.groupby(level=0).apply(_max_factor)

    # Garante que todas as arestas originais têm fator
    factors = factors.reindex(edges_gdf.index, fill_value=1.0)

    # Aplica os fatores ao campo de custo
    result = edges_gdf.copy()
    result[rule.weight_field] = result[rule.weight_field] * factors.values

    return result


# ---------------------------------------------------------------------------
# Orquestrador de múltiplas penalidades
# ---------------------------------------------------------------------------


def compose_penalties(
    edges_gdf: gpd.GeoDataFrame,
    env: EnvironmentLayers,
    rules: list[PenaltyRule],
    weight_field: str = "travel_time",
) -> gpd.GeoDataFrame:
    """Aplica múltiplas regras de penalidade em pipeline sobre as arestas.

    As regras são aplicadas **sequencialmente** de forma **acumulativa**:
    o custo de saída de uma regra vira o custo de entrada da próxima.

    .. math::

        W_{cond} = W_{base} \\times f_1 \\times f_2 \\times \\cdots \\times f_n

    Parameters
    ----------
    edges_gdf : gpd.GeoDataFrame
        Arestas da rede com geometria ``LineString``.
    env : EnvironmentLayers
        Container com todas as camadas ambientais carregadas.
    rules : list[PenaltyRule]
        Lista de regras a aplicar, na ordem desejada.
    weight_field : str, default "travel_time"
        Campo de custo a ser penalizado.

    Returns
    -------
    gpd.GeoDataFrame
        ``edges_gdf`` com ``weight_field`` atualizado acumulativamente.

    Raises
    ------
    ValueError
        Se alguma regra referencia uma camada que não existe em ``env``.
    """
    result = edges_gdf.copy()

    for rule in rules:
        # Usa o weight_field da regra, ou o fallback da função
        wf = rule.weight_field or weight_field

        if rule.layer_type == "vector":
            matching = [v for v in env.vectors if v.name == rule.layer_name]
            if not matching:
                raise ValueError(
                    f"Camada vetorial '{rule.layer_name}' não encontrada "
                    f"em EnvironmentLayers. Disponíveis: "
                    f"{[v.name for v in env.vectors]}"
                )
            layer = matching[0]

            if layer.value_column is None:
                raise ValueError(
                    f"VectorLayer '{layer.name}' não possui value_column. "
                    "Defina value_column ao carregar a camada ou "
                    "antes de chamar compose_penalties."
                )

            # Garante que a regra use o weight_field correto
            rule.weight_field = wf
            result = apply_vector_penalty(result, layer, rule)

        elif rule.layer_type == "raster":
            raise NotImplementedError(
                f"Penalidade raster para '{rule.layer_name}' ainda não "
                f"implementada. As funções raster do environment.py "
                f"precisam ser validadas primeiro."
            )

        else:
            raise ValueError(f"Tipo de camada desconhecido: {rule.layer_type}")

    return result
