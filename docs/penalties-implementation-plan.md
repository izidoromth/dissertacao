# Plano de Implementação — Módulo `penalties`

> Plano colaborativo para desenvolvimento do módulo de penalização ambiental
> do framework ambx.

## Decisões de Design

- Penalidade é mapeada por **`penalty_fn: Callable`** — função arbitrária fornecida
  pelo usuário que recebe o **valor da camada** (``Any``: ``float`` ou ``str``) e
  retorna um **fator multiplicador** (`float` ou `inf`).
- A ``penalty_fn`` usa ``Callable[[Any], float]`` para suportar tanto colunas
  numéricas quanto categóricas (ex.: ``"classe"`` com ``"Alta"``, ``"Média"``).
- Não existe modo `interdict` separado — interdição total é simplesmente
  `penalty_fn` retornando `float("inf")`.
- Para arestas que interceptam **múltiplos polígonos**, usa-se o **maior fator**
  (pior caso).
- Arestas sem interseção → fator = **1.0** (sem penalidade).
- `PenaltyRule.weight_field` mudou para `str | None` — se `None`, usa o fallback
  passado para `compose_penalties`.
- O ``PenaltyConfig`` foi considerado mas **não implementado** — `compose_penalties`
  recebe uma `list[PenaltyRule]` diretamente, sem necessidade de wrapper.
- `EnvironmentLayers` e `PenaltyRule` mantidos **separados** — as camadas são dados,
  as regras são configuração de análise. Permite múltiplos cenários com as mesmas
  camadas.
- Etapa de raster **adiada** — depende das funções raster do ``environment.py``
  serem validadas primeiro.

## Etapas

```
Etapa 1: PenaltyRule  (dataclass)
         ↓ validado
Etapa 2: apply_vector_penalty  (sjoin + penalty_fn)
         ↓ validado
Etapa 3: Teste em notebook  (dados reais da CPRM)
         ↓ validado
Etapa 4: apply_raster_penalty  (ADIADO — raster não testado)
         ↓
Etapa 5: compose_penalties  (orquestrador)
         ↓ validado
Etapa 6: Teste integrado  (múltiplas camadas + roteamento)
         ↓ validado
```

## Status Atual

| Etapa | Nome | Status | Detalhes |
|:-----:|------|:------:|----------|
| 1 | `PenaltyRule` | ✅ | Dataclass com `layer_name`, `layer_type`, `weight_field: str | None`, `penalty_fn: Callable[[Any], float]` |
| 2 | `apply_vector_penalty` | ✅ | `gpd.sjoin` + `groupby` + `reindex` com fill 1.0. Testado com camada de movimento de massa CPRM |
| 3 | Teste no notebook | ✅ | Células 20-21 no `ambx_tests.ipynb` — dataclass + compose com 3 camadas |
| 4 | Raster | ⏸️ **Adiado** | `load_raster`, `sample_raster_at_points` e `raster_stats_for_geometry` não testados |
| 5 | `compose_penalties` | ✅ | Pipeline acumulativo: `W_cond = W_base * f1 * f2 * ... * fn`. Fallback de `weight_field` |
| 6 | Teste integrado | ✅ | Comparação típico vs condicionado: merge das matrizes A*, delta tempo, pares perdidos |

## Funcionalidades Implementadas

### `PenaltyRule` (dataclass)
```python
@dataclass
class PenaltyRule:
    layer_name: str
    layer_type: Literal["raster", "vector"]
    weight_field: str | None = None       # None → fallback da compose
    penalty_fn: Callable[[Any], float] = field(default=lambda v: 1.0)
```

### `apply_vector_penalty(edges_gdf, vector_layer, rule)`
1. Verifica CRS e reprojeta se necessário
2. `gpd.sjoin` (left join, predicate="intersects")
3. Para cada aresta: aplica `rule.penalty_fn(valor)` em cada polígono,
   pega o **maior fator**
4. Arestas sem interseção → fator 1.0 (via `reindex(fill_value=1.0)`)
5. Multiplica `rule.weight_field` pelo fator

### `compose_penalties(edges_gdf, env, rules, weight_field)`
1. Itera sobre as regras na ordem fornecida
2. Para cada regra: localiza a camada em `env.vectors` pelo `layer_name`
3. Usa `rule.weight_field` ou o fallback `weight_field` da função
4. Aplica `apply_vector_penalty` sequencialmente (acumulativo)
5. `rule.layer_type == "raster"` → `NotImplementedError`

### Teste Integrado (notebook célula 22)
1. Cópia do grafo com `travel_time` sobrescrito pelos valores penalizados
2. Roteamento A* no cenário condicionado
3. Merge das matrizes típico vs condicionado
4. Cálculo de `delta_t`, `delta_pct`, pares perdidos

## Notebook — Estrutura Final

| Célula | Conteúdo |
|:------:|----------|
| 1 | Markdown — cabeçalho com etapas |
| 2 | Setup (imports) |
| 3-4 | Parâmetros |
| 5-6 | 1a — Malha |
| 7-8 | 1b — POIs |
| 9-10 | 1c — Rede |
| 11-12 | 1d — Snapping |
| 13-14 | 2 — Roteamento A\* |
| 15-16 | Mapa tempo médio |
| 17-18 | 3 — Camadas Ambientais (CPRM) |
| 19 | Markdown — Penalidades |
| **20** | **Teste PenaltyRule** (dataclass + funções) |
| **21** | **Todas as camadas + compose** (inundação, movimento, risco sintético) |
| **22** | **Comparação típico vs condicionado** (A\* + merge) |
| 23 | Resumo |

## Próximos passos (prioridade)

1. **`indicators`** — cálculo de PTh, Índice G e F15
2. **Raster** — testar `load_raster` e implementar `apply_raster_penalty`
3. **`demographics`** — compatibilização de dados censitários com a malha
4. **`comparison`** — análise comparativa entre cenários
5. **`inequality`** — análise de desigualdade socioeconômica

## Como retomar o contexto numa nova conversa

Mostre este arquivo para o Copilot com o prompt:

> "Este é o plano de implementação do módulo penalties. Já passamos da
> Etapa X. O design decisions estão neste arquivo. Vamos continuar."
