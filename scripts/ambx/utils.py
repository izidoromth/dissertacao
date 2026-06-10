"""
Utilitários geoespaciais.

Funções auxiliares para conversão de CRS, manipulação geométrica
e operações comuns reutilizadas pelos demais módulos.
"""

from shapely.geometry import base


def utm_crs(geom: base.BaseGeometry) -> str:
    """
    Retorna o código EPSG UTM adequado para o centróide da geometria.

    Parameters
    ----------
    geom : shapely.geometry.BaseGeometry
        Geometria cujo centróide será usado para determinar a zona UTM.

    Returns
    -------
    str
        Código EPSG no formato ``"EPSG:327XX"`` (hemisfério sul) ou
        ``"EPSG:326XX"`` (hemisfério norte).
    """
    lon, lat = geom.centroid.x, geom.centroid.y
    zone = int((lon + 180) / 6) + 1
    south = lat < 0
    return f"EPSG:{32700 + zone if south else 32600 + zone}"
