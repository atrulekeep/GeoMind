"""GeoMind 数据采集脚本

从 OpenStreetMap Overpass API 拉取北京真实 POI / 建筑轮廓，
经区县空间连接补充 district 字段，输出为项目标准 GeoJSON（app/data/ 目录）。

用法：
  python scripts/fetch_data.py                 # 采集全部
  python scripts/fetch_data.py hospitals       # 只采集 id 含关键字的数据集
  OVERPASS_ENDPOINT=http://内网-overpass/api/interpreter python scripts/fetch_data.py
        # 自托管 Overpass 时完全不依赖公网，实现真正私有化采集

清单中这些数据集以 source=url 声明，本脚本输出即其本地缓存文件；
缓存被删除时 registry 也会在运行时直接链接加载（GEOMIND_REFRESH=1 强制刷新）。
"""

import json
import os
import re
import sys
from pathlib import Path
from typing import Any

import geopandas as gpd
import httpx

SERVER_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SERVER_DIR))

from app.registry import DATA_DIR, osm_elements_to_geojson  # noqa: E402

OVERPASS_ENDPOINT = os.getenv(
    'OVERPASS_ENDPOINT', 'https://overpass-api.de/api/interpreter'
)

AREA_FILTER = (
    'area["name"="北京市"]["boundary"="administrative"]["admin_level"="4"]->.bj'
)

# ---------- 数据集定义 ----------

POI_DATASETS: list[dict[str, Any]] = [
    {
        'id': 'beijing_hospitals',
        'name': '北京市医院点位',
        'selectors': [
            'node["amenity"="hospital"]',
            'way["amenity"="hospital"]',
        ],
        'category': '医院',
        'require_name': True,
    },
    {
        'id': 'beijing_universities',
        'name': '北京市高等院校点位',
        'selectors': [
            'node["amenity"="university"]',
            'way["amenity"="university"]',
            'node["amenity"="college"]',
            'way["amenity"="college"]',
        ],
        'category': '高等院校',
        'require_name': True,
    },
    {
        'id': 'beijing_subway_stations',
        'name': '北京市地铁站点',
        'selectors': [
            'node["railway"="station"]["station"="subway"]',
            'node["public_transport"="station"]["subway"="yes"]',
        ],
        'category': '地铁站',
        'require_name': True,
    },
    {
        'id': 'beijing_museums',
        'name': '北京市博物馆点位',
        'selectors': [
            'node["tourism"="museum"]',
            'way["tourism"="museum"]',
        ],
        'category': '博物馆',
        'require_name': True,
    },
    {
        'id': 'beijing_heritage_sites',
        'name': '北京市历史文化遗址（人文）',
        'selectors': [
            'node["historic"]',
            'way["historic"]',
        ],
        'category': '历史遗址',
        'require_name': True,
    },
    {
        'id': 'beijing_red_sites',
        'name': '北京市红色教育基地 / 国防教育景点（公开人文 POI）',
        # 不含真实军事设施坐标；仅采集纪念地与博物馆中公开的革命/抗战/军事主题景点
        'selectors': [
            'node["historic"="memorial"]',
            'way["historic"="memorial"]',
            'node["memorial"]',
            'node["tourism"="museum"]',
            'way["tourism"="museum"]',
        ],
        'category': '红色教育基地',
        'require_name': True,
        'name_filter': (
            r'革命|烈士|抗日|抗战|红军|军事|战争|解放|起义|会师|旧址|故居|'
            r'卢沟桥|宛平|地道|新文化|五四|北大|清华|双清|香山|长辛店|铁大|焦庄户'
        ),
    },
]

# 建筑轮廓：国贸 CBD 小范围（含真实 height/levels 标签时可做真实拉伸）
BUILDING_DATASETS: list[dict[str, Any]] = [
    {
        'id': 'beijing_cbd_buildings',
        'name': '北京国贸 CBD 建筑轮廓',
        'bbox': (39.9000, 116.4450, 39.9180, 116.4750),
        'category': '建筑',
        'require_name': False,
    },
]


def build_poi_query(selectors: list[str]) -> str:
    body = ';\n  '.join(f'{s}(area.bj)' for s in selectors)
    return f'[out:json][timeout:90];\n{AREA_FILTER};\n(\n  {body};\n);\nout center;'


def build_building_query(bbox: tuple[float, float, float, float]) -> str:
    south, west, north, east = bbox
    return (
        f'[out:json][timeout:90];\n('
        f'\n  way["building"]({south},{west},{north},{east});'
        f'\n);\nout geom;'
    )


def overpass(query: str) -> dict[str, Any]:
    # Overpass 公共实例高峰期会排队导致 504/503/429，按退避重试
    last_exc: Exception | None = None
    for attempt, wait in enumerate((0, 8, 20)):
        if wait:
            import time

            time.sleep(wait)
        try:
            resp = httpx.post(
                OVERPASS_ENDPOINT,
                data={'data': query},
                timeout=120,
                follow_redirects=True,
                headers={'User-Agent': 'GeoMind/0.3 data-fetch'},
            )
            resp.raise_for_status()
            return resp.json()
        except httpx.HTTPStatusError as exc:
            last_exc = exc
            if exc.response.status_code not in (429, 500, 502, 503, 504):
                raise
            print(f'[重试 {attempt + 1}/3: HTTP {exc.response.status_code}]', end=' ', flush=True)
    raise last_exc  # type: ignore[misc]


def load_districts() -> gpd.GeoDataFrame:
    districts = gpd.read_file(DATA_DIR / 'beijing_districts.geojson')[['name', 'geometry']]
    return districts.rename(columns={'name': 'district'})


def normalize_poi(
    geojson: dict[str, Any],
    spec: dict[str, Any],
    districts: gpd.GeoDataFrame,
) -> dict[str, Any]:
    """属性精简 + 区县空间连接，统一输出字段"""
    frame = gpd.GeoDataFrame.from_features(geojson['features'], crs=4326)
    points = frame[frame.geometry.geom_type == 'Point'].copy()

    joined = gpd.sjoin(points, districts, predicate='within', how='left').drop(
        columns=['index_right'], errors='ignore'
    )

    rows: list[dict[str, Any]] = []
    name_re = re.compile(spec['name_filter']) if spec.get('name_filter') else None
    for _, row in joined.iterrows():
        # OSM 中国数据 name 常为英文，中文名优先取 name:zh
        raw_name = row.get('name:zh') or row.get('name')
        name = raw_name if isinstance(raw_name, str) and raw_name.strip() else None
        if spec.get('require_name') and not name:
            continue
        if name_re and (not name or not name_re.search(name)):
            continue
        district = row.get('district')
        rows.append(
            {
                'type': 'Feature',
                'geometry': row.geometry.__geo_interface__,
                'properties': {
                    'name': name,
                    'category': spec['category'],
                    'district': district if isinstance(district, str) else '未知',
                    'osmId': row.get('osmId', ''),
                },
            }
        )
    rows.sort(key=lambda f: (f['properties']['district'], f['properties']['name']))
    return {'type': 'FeatureCollection', 'features': rows}


def normalize_buildings(
    geojson: dict[str, Any], spec: dict[str, Any]
) -> dict[str, Any]:
    """建筑轮廓：保留多边形与 height/levels，过滤无效几何"""
    frame = gpd.GeoDataFrame.from_features(geojson['features'], crs=4326)
    polys = frame[frame.geometry.geom_type == 'Polygon'].copy()
    rows: list[dict[str, Any]] = []
    for i, (_, row) in enumerate(polys.iterrows()):
        raw_name = row.get('name:zh') or row.get('name')
        name = raw_name if isinstance(raw_name, str) and raw_name.strip() else f'建筑_{i + 1:03d}'

        def _num(key: str) -> float | None:
            val = row.get(key)
            if val is None:
                return None
            m = re.match(r'^[\d.]+', str(val))
            return float(m.group()) if m else None

        height = _num('height')
        levels = _num('building:levels')
        rows.append(
            {
                'type': 'Feature',
                'geometry': row.geometry.__geo_interface__,
                'properties': {
                    'name': name,
                    'category': spec['category'],
                    'height': height,
                    'levels': levels,
                    'osmId': row.get('osmId', ''),
                },
            }
        )
    return {'type': 'FeatureCollection', 'features': rows}


def fetch_one(spec: dict[str, Any], districts: gpd.GeoDataFrame) -> int:
    if 'bbox' in spec:
        query = build_building_query(spec['bbox'])
        payload = overpass(query)
        geojson = osm_elements_to_geojson(payload)
        result = normalize_buildings(geojson, spec)
    else:
        query = build_poi_query(spec['selectors'])
        payload = overpass(query)
        geojson = osm_elements_to_geojson(payload)
        result = normalize_poi(geojson, spec, districts)

    out = DATA_DIR / f"{spec['id']}.geojson"
    out.write_text(json.dumps(result, ensure_ascii=False), encoding='utf-8')
    return len(result['features'])


def main() -> None:
    keyword = sys.argv[1] if len(sys.argv) > 1 else None
    districts = load_districts()
    specs = POI_DATASETS + BUILDING_DATASETS
    for spec in specs:
        if keyword and keyword not in spec['id']:
            continue
        print(f"采集 {spec['id']} ...", end=' ', flush=True)
        try:
            count = fetch_one(spec, districts)
            print(f'{count} 个要素 → data/{spec["id"]}.geojson')
        except Exception as exc:
            print(f'失败：{type(exc).__name__}: {exc}')


if __name__ == '__main__':
    main()
