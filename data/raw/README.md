# Dados Brutos (Raw Data)

Dados geoespaciais utilizados como insumo para o framework **ambx**.

## Estrutura

```
data/raw/
├── README.md              ← este arquivo
└── porto_alegre/
    ├── inundacao_cprm.geojson
    └── movimento_massa_cprm.geojson
```

---

## Porto Alegre

### `inundacao_cprm.geojson`

| Campo | Descrição |
|-------|-----------|
| **Fonte** | CPRM — Serviço Geológico do Brasil |
| **Tema** | Suscetibilidade a inundações |
| **Ano** | — |
| **CRS** | EPSG:4674 (SIRGAS 2000) |
| **Feições** | ~2.500 polígonos |
| **Coluna relevante** | `classe` — `"Alto"`, `"Médio"`, `"Baixa"` |
| **Uso no ambx** | Penalização de arestas da rede por risco de inundação |

### `movimento_massa_cprm.geojson`

| Campo | Descrição |
|-------|-----------|
| **Fonte** | CPRM — Serviço Geológico do Brasil |
| **Tema** | Suscetibilidade a movimentos de massa (escorregamentos) |
| **Ano** | — |
| **CRS** | EPSG:4674 (SIRGAS 2000) |
| **Feições** | ~4.800 polígonos |
| **Coluna relevante** | `classe` — `"Alta"`, `"Média"`, `"Baixa"` |
| **Uso no ambx** | Penalização de arestas da rede por risco geológico |

> **Nota:** Ambos os arquivos foram obtidos do portal de dados geográficos da
> Prefeitura de Porto Alegre (GEOPOA).
