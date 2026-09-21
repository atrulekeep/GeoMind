"""本地数据注册中心：数据与代码解耦，同事提供的任何数据只需挂清单即可接入"""

import json
from pathlib import Path
from typing import Any

import geopandas as gpd

DATA_DIR = Path(__file__).parent / 'data'
MANIFEST = DATA_DIR / 'datasets.json'
MODELS_MANIFEST = DATA_DIR / 'models.json'


class DatasetNotFound(Exception):
    pass


class DatasetRegistry:
    def __init__(self) -> None:
        manifest = json.loads(MANIFEST.read_text(encoding='utf-8'))
        self.meta: dict[str, dict[str, Any]] = {d['id']: d for d in manifest['datasets']}
        models_manifest = json.loads(MODELS_MANIFEST.read_text(encoding='utf-8'))
        self.models: dict[str, dict[str, Any]] = {m['id']: m for m in models_manifest['models']}
        self._cache: dict[str, gpd.GeoDataFrame] = {}

    def list_models(self) -> list[dict[str, Any]]:
        return [
            {
                'id': m['id'],
                'name': m['name'],
                'defaultScale': m.get('defaultScale', 1),
                'description': m.get('description', ''),
                'attribution': m.get('attribution', ''),
            }
            for m in self.models.values()
        ]

    def get_model(self, model_id: str) -> dict[str, Any]:
        if model_id not in self.models:
            raise DatasetNotFound(
                f"模型不存在：{model_id}，可选：{list(self.models.keys())}"
            )
        return self.models[model_id]

    def list_datasets(self) -> list[dict[str, Any]]:
        return [
            {
                'id': m['id'],
                'name': m['name'],
                'kind': m['kind'],
                'sample': m.get('sample', False),
                'description': m.get('description', ''),
                'groupFields': m.get('groupFields', []),
            }
            for m in self.meta.values()
        ]

    def gdf(self, dataset_id: str) -> gpd.GeoDataFrame:
        if dataset_id not in self.meta:
            raise DatasetNotFound(f'数据集不存在：{dataset_id}')
        if dataset_id not in self._cache:
            entry = self.meta[dataset_id]
            frame = gpd.read_file(DATA_DIR / entry['path'])
            if frame.crs is not None and frame.crs.to_epsg() != 4326:
                frame = frame.to_crs(epsg=4326)
            self._cache[dataset_id] = frame
        return self._cache[dataset_id]

    def describe(self, dataset_id: str) -> dict[str, Any]:
        frame = self.gdf(dataset_id)
        props = [c for c in frame.columns if c != 'geometry']
        samples = []
        for _, row in frame.head(3).iterrows():
            samples.append({p: _safe(row.get(p)) for p in props})
        bounds = frame.total_bounds.tolist()  # minx, miny, maxx, maxy
        return {
            'id': dataset_id,
            'name': self.meta[dataset_id]['name'],
            'featureCount': len(frame),
            'fields': props,
            'samples': samples,
            'bbox': bounds,
        }

    def geojson(self, dataset_id: str) -> dict[str, Any]:
        return gdf_to_geojson(self.gdf(dataset_id))


def _jsonable(value: Any) -> Any:
    """DataV 等来源的属性（如 center/centroid）会被 geopandas 解析为 ndarray，需统一转换"""
    import numpy as np

    if value is None:
        return None
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    return value


def gdf_to_geojson(frame: gpd.GeoDataFrame) -> dict[str, Any]:
    prop_cols = [c for c in frame.columns if c != 'geometry']
    features = []
    for _, row in frame.iterrows():
        features.append(
            {
                'type': 'Feature',
                'geometry': row.geometry.__geo_interface__,
                'properties': {c: _jsonable(row[c]) for c in prop_cols},
            }
        )
    return {'type': 'FeatureCollection', 'features': features}


def _safe(value: Any) -> Any:
    if value is None:
        return None
    if hasattr(value, 'item'):
        return value.item()
    return str(value) if not isinstance(value, (int, float, bool)) else value
