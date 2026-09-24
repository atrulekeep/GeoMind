"""本地数据注册中心：数据与代码解耦

每个数据集/模型在清单中声明 source：
- {"type": "local", "path": "xxx.geojson"}        私有化本地文件（随仓库/离线可用）
- {"type": "url", "url": "https://...", "cache": "cache/xxx.geojson"}
        链接加载：首次运行时拉取并落盘缓存，之后离线可读；
        GEOMIND_REFRESH=1 可强制重新拉取。

旧格式顶层 "path": "xxx.geojson" 仍兼容，等同 local。
"""

import json
import os
from pathlib import Path
from typing import Any

import geopandas as gpd
import httpx

DATA_DIR = Path(__file__).parent / 'data'
MANIFEST = DATA_DIR / 'datasets.json'
MODELS_MANIFEST = DATA_DIR / 'models.json'

# 本地模型经 sidecar 暴露给前端时的对外基址：
# Electron 直连 127.0.0.1；Docker/Web 部署设 GEOMIND_PUBLIC_BASE='' 走同源相对路径
PUBLIC_BASE = os.getenv('GEOMIND_PUBLIC_BASE', 'http://127.0.0.1:8765').rstrip('/')

HTTP_TIMEOUT = float(os.getenv('GEOMIND_HTTP_TIMEOUT', '60'))


class DatasetNotFound(Exception):
    pass


class DatasetSourceError(Exception):
    pass


def _source_of(entry: dict[str, Any]) -> dict[str, Any]:
    """统一解析条目来源描述，兼容旧格式"""
    source = entry.get('source')
    if source is not None:
        return source
    if entry.get('path'):
        return {'type': 'local', 'path': entry['path']}
    if entry.get('url'):
        return {'type': 'url', 'url': entry['url']}
    raise DatasetSourceError(f"条目 {entry.get('id')} 缺少 source/path/url 声明")


def _safe_child(base: Path, relative: str) -> Path:
    """拼接并校验子路径，防止 ../ 穿越数据目录"""
    target = (base / relative).resolve()
    if base.resolve() not in target.parents and target != base.resolve():
        raise DatasetSourceError(f'非法的数据路径：{relative}')
    return target


def _fetch_url(url: str) -> Any:
    """同步拉取 URL，返回解析后的 JSON"""
    try:
        resp = httpx.get(
            url,
            timeout=HTTP_TIMEOUT,
            follow_redirects=True,
            headers={'User-Agent': 'GeoMind/0.3 data-fetch'},
        )
        resp.raise_for_status()
        return resp.json()
    except httpx.HTTPError as exc:
        raise DatasetSourceError(f'链接数据拉取失败：{url} ({exc})') from exc


def osm_elements_to_geojson(payload: dict[str, Any]) -> dict[str, Any]:
    """Overpass `out geom;` 的 OSM JSON → GeoJSON（轻量转换，无第三方依赖）

    node 带 tags → Point；way 带 geometry → 闭合为 Polygon，否则 LineString；
    relation 在 POI 场景不使用，跳过。
    """
    features: list[dict[str, Any]] = []
    for el in payload.get('elements', []):
        el_type = el.get('type')
        props = dict(el.get('tags', {}))
        props['osmId'] = f"{el_type}/{el.get('id')}"
        if el_type == 'node':
            if 'lat' not in el or 'lon' not in el:
                continue
            geom = {'type': 'Point', 'coordinates': [el['lon'], el['lat']]}
        elif el_type == 'way':
            # out center：way 型 POI 只回中心坐标
            if 'center' in el:
                geom = {
                    'type': 'Point',
                    'coordinates': [el['center']['lon'], el['center']['lat']],
                }
            else:
                coords = [[p['lon'], p['lat']] for p in el.get('geometry', [])]
                if len(coords) < 2:
                    continue
                if coords[0] == coords[-1] and len(coords) >= 4:
                    geom = {'type': 'Polygon', 'coordinates': [coords]}
                else:
                    geom = {'type': 'LineString', 'coordinates': coords}
        else:
            continue
        features.append({'type': 'Feature', 'geometry': geom, 'properties': props})
    return {'type': 'FeatureCollection', 'features': features}


class DatasetRegistry:
    def __init__(self) -> None:
        manifest = json.loads(MANIFEST.read_text(encoding='utf-8'))
        self.meta: dict[str, dict[str, Any]] = {d['id']: d for d in manifest['datasets']}
        models_manifest = json.loads(MODELS_MANIFEST.read_text(encoding='utf-8'))
        self.models: dict[str, dict[str, Any]] = {m['id']: m for m in models_manifest['models']}
        self._cache: dict[str, gpd.GeoDataFrame] = {}

    # ---------- models ----------

    def _model_url(self, m: dict[str, Any]) -> str:
        """把模型来源解析为前端可直接加载的 URL"""
        source = _source_of(m)
        if source['type'] == 'url':
            return source['url']
        # local：经 sidecar /api/assets 路由提供（相对 DATA_DIR）
        return f'{PUBLIC_BASE}/api/assets/{source["path"].lstrip("/")}'

    def list_models(self) -> list[dict[str, Any]]:
        return [
            {
                'id': m['id'],
                'name': m['name'],
                'defaultScale': m.get('defaultScale', 1),
                'description': m.get('description', ''),
                'attribution': m.get('attribution', ''),
                'sourceType': _source_of(m)['type'],
            }
            for m in self.models.values()
        ]

    def get_model(self, model_id: str) -> dict[str, Any]:
        if model_id not in self.models:
            raise DatasetNotFound(
                f"模型不存在：{model_id}，可选：{list(self.models.keys())}"
            )
        m = self.models[model_id]
        return {**m, 'url': self._model_url(m)}

    # ---------- datasets ----------

    def list_datasets(self) -> list[dict[str, Any]]:
        return [
            {
                'id': m['id'],
                'name': m['name'],
                'kind': m['kind'],
                'sample': m.get('sample', False),
                'description': m.get('description', ''),
                'groupFields': m.get('groupFields', []),
                'sourceType': _source_of(m)['type'],
            }
            for m in self.meta.values()
        ]

    def _ensure_local_file(self, dataset_id: str, entry: dict[str, Any]) -> Path:
        """返回数据集对应的本地文件；url 型按需拉取并缓存"""
        source = _source_of(entry)
        if source['type'] == 'local':
            return _safe_child(DATA_DIR, source['path'])

        # url 型：优先读缓存（私有化/离线）
        cache_rel = source.get('cache') or f'cache/{dataset_id}.geojson'
        cache_path = _safe_child(DATA_DIR, cache_rel)
        refresh = os.getenv('GEOMIND_REFRESH', '').strip() in ('1', 'true', 'yes')
        if cache_path.exists() and not refresh:
            return cache_path

        payload = _fetch_url(source['url'])
        if source.get('format', 'geojson') == 'osm-json':
            payload = osm_elements_to_geojson(payload)
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(
            json.dumps(payload, ensure_ascii=False), encoding='utf-8'
        )
        return cache_path

    def gdf(self, dataset_id: str) -> gpd.GeoDataFrame:
        if dataset_id not in self.meta:
            raise DatasetNotFound(f'数据集不存在：{dataset_id}')
        if dataset_id not in self._cache:
            entry = self.meta[dataset_id]
            path = self._ensure_local_file(dataset_id, entry)
            frame = gpd.read_file(path)
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
            'sourceType': _source_of(self.meta[dataset_id])['type'],
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
    # ndarray / numpy 标量先走通用转换，避免对多元素数组直接 .item()
    value = _jsonable(value)
    if isinstance(value, list):
        return json.dumps(value, ensure_ascii=False)
    if hasattr(value, 'item'):
        return value.item()
    return value if isinstance(value, (int, float, bool)) else str(value)
