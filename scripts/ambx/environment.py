"""
Módulo de carregamento de camadas ambientais (raster / vetorial).

Oferece funções para ler, recortar e organizar dados geoespaciais
de origem ambiental que servem como insumo para as funções de
penalização do módulo ``penalties``.

Suporta dois tipos de camada:

- **Vetoriais** — polígonos representando manchas de inundação,
  áreas verdes, zonas de risco geológico, etc.
- **Raster** — grades regulares contínuas como temperatura de
  superfície, altimetria, declividade, etc.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import geopandas as gpd
import numpy as np
import pandas as pd
import rasterio
from rasterio.mask import mask as rio_mask
from rasterio.warp import transform_bounds
from shapely.geometry import box, Polygon

from ambx.utils import utm_crs

# ---------------------------------------------------------------------------
# Estruturas de dados
# ---------------------------------------------------------------------------


@dataclass
class RasterLayer:
    """
    Camada ambiental do tipo raster.

    Attributes
    ----------
    name : str
        Nome descritivo da camada (ex.: "temperatura_superficie").
    data : np.ndarray
        Array 2D com os valores do raster (recortado pela área de
        estudo, se aplicável).
    bounds : tuple[float, float, float, float]
        Limites geográficos do raster no CRS de origem
        ``(minx, miny, maxx, maxy)``.
    crs : str
        CRS do raster (ex.: ``"EPSG:4326"``).
    transform : rasterio.Affine
        Transformação affine para mapear coordenadas do array.
    nodata : float | None
        Valor considerado como nodata.
    source_path : str | None
        Caminho do arquivo de origem (se carregado de arquivo).
    """

    name: str
    data: np.ndarray
    bounds: tuple[float, float, float, float]
    crs: str
    transform: rasterio.Affine
    nodata: float | None = None
    source_path: str | None = None

    # TODO (não testado): as propriedades e métodos de RasterLayer
    # ainda não foram validadas com dados reais.

    @property
    def shape(self) -> tuple[int, int]:
        """Dimensões do array ``(altura, largura)``."""
        return self.data.shape

    @property
    def resolution(self) -> tuple[float, float]:
        """Resolução espacial ``(dx, dy)`` em unidades do CRS."""
        return abs(self.transform.a), abs(self.transform.e)


@dataclass
class VectorLayer:
    """
    Camada ambiental do tipo vetorial.

    Attributes
    ----------
    name : str
        Nome descritivo da camada (ex.: "inundacao_2024").
    gdf : gpd.GeoDataFrame
        GeoDataFrame com os feições carregadas, já recortado pela
        área de estudo (se aplicável).
    value_column : str | None
        Nome da coluna em ``gdf`` com o valor de penalidade
        associado a cada feição. Se ``None``, assume penalidade
        uniforme (ex.: interdição total).
    source_path : str | None
        Caminho do arquivo de origem (se carregado de arquivo).
    """

    name: str
    gdf: gpd.GeoDataFrame
    value_column: str | None = None
    source_path: str | None = None


@dataclass
class EnvironmentLayers:
    """
    Agregador de todas as camadas ambientais carregadas para uma
    área de estudo.

    Este objeto é o principal contrato entre os módulos
    ``environment`` e ``penalties``: as funções de penalização
    recebem um ``EnvironmentLayers`` e aplicam as penalidades
    sobre a rede.

    Attributes
    ----------
    area_of_interest : Polygon | None
        Polígono da área de estudo utilizado para recorte.
    rasters : list[RasterLayer]
        Lista de camadas raster carregadas.
    vectors : list[VectorLayer]
        Lista de camadas vetoriais carregadas.
    crs_utm : str | None
        CRS UTM de referência para a área de estudo (preenchido
        automaticamente na criação).
    """

    area_of_interest: Polygon | None = None
    rasters: list[RasterLayer] = field(default_factory=list)
    vectors: list[VectorLayer] = field(default_factory=list)
    crs_utm: str | None = None

    def __post_init__(self):
        if self.area_of_interest is not None and self.crs_utm is None:
            self.crs_utm = utm_crs(self.area_of_interest)

    @property
    def num_rasters(self) -> int:
        return len(self.rasters)

    @property
    def num_vectors(self) -> int:
        return len(self.vectors)

    def add_raster(self, layer: RasterLayer):
        """Adiciona uma camada raster à coleção."""
        self.rasters.append(layer)

    def add_vector(self, layer: VectorLayer):
        """Adiciona uma camada vetorial à coleção."""
        self.vectors.append(layer)

    def __repr__(self) -> str:
        return (
            f"EnvironmentLayers("
            f"rasters={self.num_rasters}, "
            f"vectors={self.num_vectors}, "
            f"crs_utm={self.crs_utm})"
        )


# ---------------------------------------------------------------------------
# Leitura de camadas vetoriais
# ---------------------------------------------------------------------------


def load_vector(
    path: str | Path,
    name: str | None = None,
    value_column: str | None = None,
    clip_geometry: Polygon | None = None,
    aoi_crs: str | None = None,
    expected_crs: str | None = None,
) -> VectorLayer:
    """
    Carrega uma camada vetorial de um arquivo.

    Suporta qualquer formato lido pelo GeoPandas
    (Shapefile, GeoJSON, GeoPackage, etc.).

    Parameters
    ----------
    path : str | Path
        Caminho do arquivo vetorial.
    name : str | None, default None
        Nome da camada. Se ``None``, usa o nome do arquivo (sem extensão).
    value_column : str | None, default None
        Coluna com valor de penalidade da feição.
    clip_geometry : Polygon | None, default None
        Polígono para recorte espacial da camada.
    aoi_crs: str | None, default None
        CRS da geometria de recorte. Se ``None``, assume o mesmo CRS da camada.
    expected_crs : str | None, default None
        CRS esperado para a camada. Se fornecido e a camada estiver
        em outro CRS, será reprojetada automaticamente.

    Returns
    -------
    VectorLayer
        Camada vetorial carregada.

    Raises
    ------
    FileNotFoundError
        Se o arquivo não existir.
    ValueError
        Se ``value_column`` for informada e não existir no arquivo.
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Arquivo vetorial não encontrado: {path}")

    if name is None:
        name = path.stem

    gdf = gpd.read_file(path)

    if gdf.empty:
        raise ValueError(f"Arquivo vetorial vazio ou sem feições: {path}")

    if value_column is not None and value_column not in gdf.columns:
        raise ValueError(
            f"Coluna '{value_column}' não encontrada em {path}. "
            f"Colunas disponíveis: {list(gdf.columns)}"
        )

    # Reprojeção se necessário
    if expected_crs is not None and gdf.crs is not None:
        if gdf.crs.to_string() != expected_crs:
            gdf = gdf.to_crs(expected_crs)

    # Recorte espacial
    if clip_geometry is not None:
        clip_gdf = gpd.GeoDataFrame(geometry=[clip_geometry], crs=aoi_crs)
        clip_gdf = clip_gdf.to_crs(gdf.crs)
        gdf = gpd.clip(gdf, clip_gdf)

    return VectorLayer(
        name=name,
        gdf=gdf,
        value_column=value_column,
        source_path=str(path.resolve()),
    )


def load_vector_from_gdf(
    gdf: gpd.GeoDataFrame,
    name: str = "vector_layer",
    value_column: str | None = None,
    clip_geometry: Polygon | None = None,
    aoi_crs: str | None = None,
    expected_crs: str | None = None,
) -> VectorLayer:
    """
    Cria uma ``VectorLayer`` a partir de um GeoDataFrame existente.

    Útil para quando a camada já está em memória (ex.: carregada
    de um notebook, de uma API ou gerada programaticamente).

    Parameters
    ----------
    gdf : gpd.GeoDataFrame
        GeoDataFrame com as feições.
    name : str, default "vector_layer"
        Nome descritivo da camada.
    value_column : str | None, default None
        Coluna com valor de penalidade.
    clip_geometry : Polygon | None, default None
        Polígono para recorte espacial.
    aoi_crs : str | None, default None
        CRS da geometria de recorte. Se ``None``, assume o mesmo CRS da camada.
    expected_crs : str | None, default None
        CRS esperado; faz reprojeção se necessário.

    Returns
    -------
    VectorLayer
    """
    gdf = gdf.copy()

    if expected_crs is not None and gdf.crs is not None:
        if gdf.crs.to_string() != expected_crs:
            gdf = gdf.to_crs(expected_crs)

    if clip_geometry is not None:
        clip_gdf = gpd.GeoDataFrame(geometry=[clip_geometry], crs=aoi_crs)
        clip_gdf = clip_gdf.to_crs(gdf.crs)
        gdf = gpd.clip(gdf, clip_gdf)

    if value_column is not None and value_column not in gdf.columns:
        raise ValueError(
            f"Coluna '{value_column}' não encontrada no GeoDataFrame. "
            f"Colunas disponíveis: {list(gdf.columns)}"
        )

    return VectorLayer(
        name=name,
        gdf=gdf,
        value_column=value_column,
    )


# ---------------------------------------------------------------------------
# Leitura de camadas raster
# ---------------------------------------------------------------------------
# TODO (não testado): Toda a seção de raster abaixo ainda não foi
# testada com dados reais. Inclui:
#   - load_raster (recorte por geometria, bounding box, reprojeção)
#   - load_raster_from_array
#   - raster_stats_for_geometry
#   - sample_raster_at_points
#   - build_environment (parte de raster_paths)
# ---------------------------------------------------------------------------


def load_raster(
    path: str | Path,
    name: str | None = None,
    band: int = 1,
    clip_geometry: Polygon | None = None,
    aoi_crs: str | None = None,
    clip_bounds: tuple[float, float, float, float] | None = None,
    dst_crs: str | None = None,
) -> RasterLayer:
    """
    Carrega uma camada raster de um arquivo GeoTIFF.

    Parameters
    ----------
    path : str | Path
        Caminho do arquivo raster.
    name : str | None, default None
        Nome da camada. Se ``None``, usa o nome do arquivo (sem extensão).
    band : int, default 1
        Banda a ser carregada (1-based).
    clip_geometry : Polygon | None, default None
        Geometria (no CRS do raster) para recortar o raster.
        Se informado, ``clip_bounds`` é ignorado.
    clip_bounds : tuple[float, float, float, float] | None, default None
        Limites ``(minx, miny, maxx, maxy)`` para recorte por bounding box.
        Ignorado se ``clip_geometry`` for fornecido.
    dst_crs : str | None, default None
        CRS de destino para reprojetar o raster antes de recortar.
        Se ``None``, mantém o CRS original.

    Returns
    -------
    RasterLayer
        Camada raster carregada.

    Raises
    ------
    FileNotFoundError
        Se o arquivo não existir.
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Arquivo raster não encontrado: {path}")

    if name is None:
        name = path.stem

    with rasterio.open(path) as src:
        geom_to_mask = None
        out_bounds = None
        out_crs = dst_crs or src.crs

        if clip_geometry is not None:
            # Projetar geometria de recorte para o CRS do raster
            clip_gdf = gpd.GeoDataFrame(geometry=[clip_geometry], crs=aoi_crs)
            clip_gdf = clip_gdf.to_crs(src.crs.to_string())
            geom_to_mask = clip_gdf.geometry.iloc[0]

            out_image, out_transform = rio_mask(
                src, [geom_to_mask], crop=True
            )
            out_bounds = (
                out_transform.c,
                out_transform.f - out_transform.e * out_image.shape[0],
                out_transform.c + out_transform.a * out_image.shape[1],
                out_transform.f,
            )

        elif clip_bounds is not None:
            # Recorte por bounding box
            minx, miny, maxx, maxy = clip_bounds
            if src.crs and dst_crs and src.crs.to_string() != dst_crs:
                # Transforma bounds para o CRS do raster
                minx, miny, maxx, maxy = transform_bounds(
                    dst_crs, src.crs, minx, miny, maxx, maxy
                )
            bbox = box(minx, miny, maxx, maxy)
            out_image, out_transform = rio_mask(src, [bbox], crop=True)
            out_bounds = (
                out_transform.c,
                out_transform.f - out_transform.e * out_image.shape[0],
                out_transform.c + out_transform.a * out_image.shape[1],
                out_transform.f,
            )

        else:
            # Carrega raster completo
            out_image = src.read(band)
            out_transform = src.transform
            out_bounds = (
                src.bounds.left,
                src.bounds.bottom,
                src.bounds.right,
                src.bounds.top,
            )

        data = out_image.squeeze()
        nodata = src.nodata

        # Reprojetar o array se dst_crs for diferente do CRS original
        if dst_crs and src.crs and src.crs.to_string() != dst_crs:
            from rasterio.warp import reproject, Resampling, calculate_default_transform

            # Prepara bounds de saída no CRS destino
            left, bottom, right, top = out_bounds
            if src.crs:
                dst_transform, dst_width, dst_height = calculate_default_transform(
                    src.crs, dst_crs, data.shape[1], data.shape[0],
                    left=left, bottom=bottom, right=right, top=top,
                )
            else:
                dst_transform, dst_width, dst_height = out_transform, data.shape[1], data.shape[0]

            dst_data = np.empty((dst_height, dst_width), dtype=data.dtype)

            reproject(
                source=data,
                src_transform=out_transform,
                src_crs=src.crs,
                src_nodata=nodata,
                destination=dst_data,
                dst_transform=dst_transform,
                dst_crs=dst_crs,
                dst_nodata=nodata,
                resampling=Resampling.bilinear,
            )

            data = dst_data
            out_transform = dst_transform
            out_bounds = (
                dst_transform.c,
                dst_transform.f - dst_transform.e * dst_height,
                dst_transform.c + dst_transform.a * dst_width,
                dst_transform.f,
            )
            out_crs = dst_crs

        return RasterLayer(
            name=name,
            data=data,
            bounds=out_bounds,
            crs=out_crs or "EPSG:4326",
            transform=out_transform,
            nodata=nodata,
            source_path=str(path.resolve()),
        )


# TODO (não testado)
def load_raster_from_array(
    data: np.ndarray,
    bounds: tuple[float, float, float, float],
    crs: str,
    name: str = "raster_layer",
    nodata: float | None = None,
) -> RasterLayer:
    """
    Cria uma ``RasterLayer`` a partir de um array NumPy.

    Útil para quando o raster já está em memória (ex.: resultado de
    processamento, dados de API, etc.).

    Parameters
    ----------
    data : np.ndarray
        Array 2D com os valores.
    bounds : tuple[float, float, float, float]
        Limites geográficos ``(minx, miny, maxx, maxy)`` no CRS.
    crs : str
        CRS do raster.
    name : str, default "raster_layer"
        Nome descritivo da camada.
    nodata : float | None, default None
        Valor nodata.

    Returns
    -------
    RasterLayer
    """
    if data.ndim == 3:
        data = data.squeeze()
    if data.ndim != 2:
        raise ValueError(f"Array deve ser 2D, mas tem shape {data.shape}")

    height, width = data.shape
    minx, miny, maxx, maxy = bounds

    transform = rasterio.Affine(
        (maxx - minx) / width,
        0.0,
        minx,
        0.0,
        -(maxy - miny) / height,
        maxy,
    )

    return RasterLayer(
        name=name,
        data=data,
        bounds=bounds,
        crs=crs,
        transform=transform,
        nodata=nodata,
    )


# ---------------------------------------------------------------------------
# Funções auxiliares
# ---------------------------------------------------------------------------


# TODO (não testado)
def raster_stats_for_geometry(
    raster: RasterLayer,
    geometry: Polygon | gpd.GeoDataFrame,
    statistic: Literal["mean", "sum", "min", "max", "std"] = "mean",
) -> float | None:
    """
    Calcula uma estatística zonal dos valores do raster para uma
    geometria.

    Parameters
    ----------
    raster : RasterLayer
        Camada raster a ser amostrada.
    geometry : Polygon | gpd.GeoDataFrame
        Geometria sobre a qual calcular a estatística.
    statistic : Literal["mean", "sum", "min", "max", "std"], default "mean"
        Estatística a ser calculada.

    Returns
    -------
    float | None
        Valor da estatística, ou ``None`` se a geometria não
        interceptar o raster.
    """
    import rasterio.features

    if isinstance(geometry, gpd.GeoDataFrame):
        geometry = geometry.geometry.union_all()

    # Transformar geometria para o CRS do raster
    from shapely.ops import transform as shapely_transform
    import pyproj

    geom_crs = _get_crs_str(geometry)
    if geom_crs and geom_crs != raster.crs:
        project = pyproj.Transformer.from_crs(
            geom_crs, raster.crs, always_xy=True
        ).transform
        geometry = shapely_transform(project, geometry)

    # Extrair pixels sob a geometria
    try:
        out_image, _ = rio_mask(
            None,
            [geometry],
            crop=True,
            all_touched=True,
            transform=raster.transform,
            height=raster.data.shape[0],
            width=raster.data.shape[1],
            nodata=raster.nodata or 0,
        )
    except Exception:
        return None

    masked = np.ma.MaskedArray(
        out_image.squeeze(),
        mask=(out_image.squeeze() == raster.nodata) if raster.nodata is not None else False,
    )

    if masked.count() == 0:
        return None

    stats_map = {
        "mean": masked.mean(),
        "sum": masked.sum(),
        "min": masked.min(),
        "max": masked.max(),
        "std": masked.std(),
    }

    return float(stats_map.get(statistic, masked.mean()))


# TODO (não testado)
def sample_raster_at_points(
    raster: RasterLayer,
    points: gpd.GeoDataFrame,
    column_name: str | None = None,
) -> pd.Series:
    """
    Amostra os valores do raster nas coordenadas dos pontos.

    Parameters
    ----------
    raster : RasterLayer
        Camada raster.
    points : gpd.GeoDataFrame
        Pontos de amostragem (qualquer CRS).
    column_name : str | None, default None
        Nome para a coluna resultante. Se ``None``, usa o
        nome do raster.

    Returns
    -------
    pd.Series
        Série com os valores amostrados, indexada pelo índice
        de ``points``.
    """
    from rasterio.sample import sample_gen

    # Projetar pontos para o CRS do raster
    pts_utm = points.to_crs(raster.crs) if points.crs else points

    coords = [(pt.x, pt.y) for pt in pts_utm.geometry]
    samples = list(sample_gen(raster.data, coords, transform=raster.transform))

    values = [
        s[0] if s[0] != raster.nodata else np.nan
        for s in samples
    ]

    col_name = column_name or raster.name
    return pd.Series(values, index=points.index, name=col_name, dtype=float)


def _get_crs_str(geometry: Any) -> str | None:
    """Extrai o CRS como string de uma geometria ou GeoDataFrame."""
    if isinstance(geometry, gpd.GeoDataFrame):
        return geometry.crs.to_string() if geometry.crs else None
    if hasattr(geometry, "crs"):
        return geometry.crs.to_string() if geometry.crs else None
    return None


# ---------------------------------------------------------------------------
# Criação do container principal
# ---------------------------------------------------------------------------


def build_environment(
    area_of_interest: Polygon | gpd.GeoDataFrame,
    raster_paths: list[str | Path] | None = None,
    vector_paths: list[str | Path] | None = None,
    vector_gdfs: list[tuple[gpd.GeoDataFrame, str]] | None = None,
    raster_value_columns: dict[str, str] | None = None,
) -> EnvironmentLayers:
    """
    Constrói o container ``EnvironmentLayers`` para uma área de
    estudo, carregando todas as camadas especificadas.

    Este é o entry-point de alto nível do módulo: recebe listas
    de arquivos e/ou GeoDataFrames, carrega cada camada (com
    recorte automático pela área de interesse) e retorna o
    container pronto para uso no módulo ``penalties``.

    Parameters
    ----------
    area_of_interest : Polygon | gpd.GeoDataFrame
        Área de estudo. Se for um GeoDataFrame, usa a geometria
        unida. O CRS UTM é determinado automaticamente.
    raster_paths : list[str | Path] | None, default None
        Lista de caminhos de arquivos raster (GeoTIFF) a carregar.
    vector_paths : list[str | Path] | None, default None
        Lista de caminhos de arquivos vetoriais a carregar.
    vector_gdfs : list[tuple[gpd.GeoDataFrame, str]] | None, default None
        Lista de tuplas ``(gdf, name)`` com GeoDataFrames já em
        memória para carregar como camadas vetoriais.
    raster_value_columns : dict[str, str] | None, default None
        Mapeamento ``{caminho_do_raster: nome_da_coluna}`` para
        indicar qual coluna usar como valor de penalidade em
        camadas vetoriais. Se não informado, assume penalidade
        uniforme para as camadas vetoriais.

    Returns
    -------
    EnvironmentLayers
        Container com todas as camadas carregadas.

    Examples
    --------
    >>> env = build_environment(
    ...     area_of_interest=city_boundary,
    ...     raster_paths=["dados/temperatura.tif"],
    ...     vector_paths=["dados/inundacao.geojson"],
    ... )
    >>> env.num_rasters
    1
    >>> env.num_vectors
    1
    """
    # Normalizar área de interesse
    if isinstance(area_of_interest, gpd.GeoDataFrame):
        aoi_geom = area_of_interest.geometry.union_all()
        aoi_crs = area_of_interest.crs
    else:
        aoi_geom = area_of_interest
        aoi_crs = None

    env = EnvironmentLayers(area_of_interest=aoi_geom)

    # Carregar rasters
    # TODO (não testado): o carregamento de rasters em build_environment
    # ainda não foi testado com dados reais.
    if raster_paths:
        for rpath in raster_paths:
            raster = load_raster(
                rpath,
                clip_geometry=aoi_geom,
                aoi_crs=aoi_crs,
                dst_crs=env.crs_utm,
            )
            env.add_raster(raster)

    # Carregar vetoriais de arquivos
    if vector_paths:
        vcols = raster_value_columns or {}
        for vpath in vector_paths:
            vpath_str = str(vpath)
            layer = load_vector(
                vpath,
                clip_geometry=aoi_geom,
                aoi_crs=aoi_crs,
                expected_crs=env.crs_utm,
                value_column=vcols.get(vpath_str),
            )
            env.add_vector(layer)

    # Carregar vetoriais de GeoDataFrames
    if vector_gdfs:
        for gdf, name in vector_gdfs:
            layer = load_vector_from_gdf(
                gdf,
                name=name,
                clip_geometry=aoi_geom,
                aoi_crs=aoi_crs,
                expected_crs=env.crs_utm,
            )
            env.add_vector(layer)

    return env
