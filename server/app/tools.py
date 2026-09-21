"""GIS 工具集：LLM 只能引用数据集 id 与字段，绝不接触坐标本身"""

import inspect
from typing import Any, Callable

import geopandas as gpd

from .knowledge import search_knowledge
from .registry import DatasetRegistry, gdf_to_geojson
from .schemas import (
    AggregateInput,
    DatasetIdInput,
    ModelPosition,
    RenderSceneInput,
    ResolvedCamera,
    ResolvedSceneLayer,
    SceneLayerInput,
    SceneSpec,
    SearchKnowledgeInput,
)

Handler = Callable[
    [DatasetRegistry, 'SceneStore', dict[str, Any]],
    dict[str, Any] | Any,  # 允许返回 coroutine（异步 handler）
]


class SceneStore:
    def __init__(self) -> None:
        self.current: SceneSpec | None = None


def _camera_from_bbox(bbox: list[float]) -> ResolvedCamera:
    minx, miny, maxx, maxy = bbox
    span = max(maxx - minx, maxy - miny)
    return ResolvedCamera(
        lon=(minx + maxx) / 2,
        lat=(miny + maxy) / 2,
        height=max(span * 111_000 * 1.8, 3000),
    )


# ---------- handlers ----------


def t_list_datasets(reg: DatasetRegistry, _store: SceneStore, _args: dict) -> dict[str, Any]:
    return {'datasets': reg.list_datasets(), 'models': reg.list_models()}


def t_describe_dataset(reg: DatasetRegistry, _store: SceneStore, args: dict) -> dict[str, Any]:
    parsed = DatasetIdInput.model_validate(args)
    return reg.describe(parsed.datasetId)


def t_aggregate(reg: DatasetRegistry, _store: SceneStore, args: dict) -> dict[str, Any]:
    parsed = AggregateInput.model_validate(args)
    points = reg.gdf(parsed.pointsId)
    polygons = reg.gdf(parsed.polygonsId)
    if parsed.groupField not in polygons.columns:
        raise ValueError(f"面数据不存在字段 '{parsed.groupField}'，可选：{[c for c in polygons.columns if c != 'geometry']}")

    pts = points[points.geometry.geom_type == 'Point'][['geometry']]
    joined = gpd.sjoin(
        pts,
        polygons[[parsed.groupField, 'geometry']],
        predicate='within',
        how='inner',
    )
    counts = joined.groupby(parsed.groupField).size()
    rows = [
        {'group': str(name), 'count': int(counts.get(name, 0))}
        for name in polygons[parsed.groupField]
    ]
    return {'matchedPoints': int(len(joined)), 'regionCount': len(rows), 'counts': rows}


def _resolve_vector_layer(reg: DatasetRegistry, layer: SceneLayerInput) -> ResolvedSceneLayer:
    frame = reg.gdf(layer.datasetId).copy()
    height_property = None
    if layer.metricValues:
        if not layer.metricGroupField:
            raise ValueError('提供 metricValues 时必须同时提供 metricGroupField')
        metric_name = layer.metricName or 'metric'
        mapping = {m.group: m.value for m in layer.metricValues}
        frame[metric_name] = frame[layer.metricGroupField].map(
            lambda key: mapping.get(str(key), 0.0)
        )
        height_property = metric_name

    geojson = gdf_to_geojson(frame)
    return ResolvedSceneLayer(
        id=layer.id,
        kind=layer.kind,
        geojson=geojson,
        color=layer.color,
        heightProperty=(
            height_property if layer.kind == 'polygon-extrusion' else None
        ),
        heightScale=layer.heightScale,
        pointSize=layer.pointSize,
    )


def _resolve_model_layer(reg: DatasetRegistry, layer: SceneLayerInput) -> ResolvedSceneLayer:
    if not layer.modelId or not layer.nameValue:
        raise ValueError("gltf-model 图层必须提供 modelId 与 nameValue（锚点点名）")
    model = reg.get_model(layer.modelId)

    frame = reg.gdf(layer.datasetId)
    field = layer.nameField or 'name'
    if field not in frame.columns:
        raise ValueError(f"点数据不存在字段 '{field}'，可选：{[c for c in frame.columns if c != 'geometry']}")
    match = frame[frame[field].astype(str) == layer.nameValue]
    if match.empty:
        available = frame[field].astype(str).tolist()
        raise ValueError(f"未找到 {field}='{layer.nameValue}' 的点，可选：{available}")

    geom = match.geometry.iloc[0]
    if geom.geom_type != 'Point':
        raise ValueError(f"锚点要素必须是 Point，实际为 {geom.geom_type}")

    return ResolvedSceneLayer(
        id=layer.id,
        kind='gltf-model',
        url=model['url'],
        position=ModelPosition(lon=geom.x, lat=geom.y, height=layer.heightOffset or 0),
        scale=layer.scale if layer.scale is not None else model.get('defaultScale', 1),
        headingDegrees=layer.headingDegrees or 0,
    )


def t_render_scene(reg: DatasetRegistry, store: SceneStore, args: dict) -> dict[str, Any]:
    parsed = RenderSceneInput.model_validate(args)
    resolved: list[ResolvedSceneLayer] = []
    for layer in parsed.layers:
        if layer.kind == 'gltf-model':
            resolved.append(_resolve_model_layer(reg, layer))
        else:
            resolved.append(_resolve_vector_layer(reg, layer))

    camera = None
    if parsed.cameraDatasetId:
        bbox = reg.gdf(parsed.cameraDatasetId).total_bounds.tolist()
        camera = _camera_from_bbox(bbox)

    incoming = SceneSpec(layers=resolved, camera=camera,
                         basemap=parsed.basemap, terrain=parsed.terrain)
    if parsed.replaceScene or store.current is None:
        store.current = incoming
        merged_ids = [l.id for l in resolved]
    else:
        # 多轮 patch：同 id 替换、新 id 追加、未提及的图层/底图/地形保留
        prev = store.current
        incoming_ids = {l.id for l in resolved}
        kept = [l for l in prev.layers if l.id not in incoming_ids]
        store.current = SceneSpec(
            layers=kept + resolved,
            camera=camera or prev.camera,
            basemap=incoming.basemap or prev.basemap,
            terrain=incoming.terrain or prev.terrain,
        )
        merged_ids = [l.id for l in store.current.layers]
    return {
        'ok': True,
        'layerCount': len(store.current.layers),
        'replaced': parsed.replaceScene,
        'layerIds': merged_ids,
        'cameraSet': camera is not None,
        'basemap': store.current.basemap,
        'terrain': store.current.terrain,
    }


async def t_search_knowledge(
    _reg: DatasetRegistry, _store: SceneStore, args: dict
) -> dict[str, Any]:
    parsed = SearchKnowledgeInput.model_validate(args)
    return await search_knowledge(parsed.query, parsed.k)


def t_fly_to(reg: DatasetRegistry, store: SceneStore, args: dict) -> dict[str, Any]:
    parsed = DatasetIdInput.model_validate(args)
    camera = _camera_from_bbox(reg.gdf(parsed.datasetId).total_bounds.tolist())
    if store.current is not None:
        store.current.camera = camera
    return {'ok': True, 'camera': camera.model_dump()}


HANDLERS: dict[str, Handler] = {
    'list_datasets': t_list_datasets,
    'describe_dataset': t_describe_dataset,
    'aggregate_points_by_region': t_aggregate,
    'render_scene': t_render_scene,
    'fly_to': t_fly_to,
    'search_knowledge': t_search_knowledge,
}


async def dispatch(
    name: str, args: dict[str, Any], reg: DatasetRegistry, store: SceneStore
) -> dict[str, Any]:
    handler = HANDLERS.get(name)
    if handler is None:
        return {'error': f'未知工具：{name}'}
    try:
        result = handler(reg, store, args)
        if inspect.isawaitable(result):
            result = await result
        return result
    except Exception as exc:  # 异常回喂给 LLM，允许它自我修正
        return {'error': f'{type(exc).__name__}: {exc}'}


# ---------- OpenAI function-calling 规格 ----------


def _spec(
    name: str, description: str, model: type | None
) -> dict[str, Any]:
    parameters = model.model_json_schema() if model else {'type': 'object', 'properties': {}}
    parameters.pop('title', None)
    return {
        'type': 'function',
        'function': {'name': name, 'description': description, 'parameters': parameters},
    }


TOOL_SPECS: list[dict[str, Any]] = [
    _spec('list_datasets', '列出所有可用数据集（id、名称、类型、可分组字段）以及可用的 glb 三维模型（models）', None),
    _spec('describe_dataset', '查看数据集的要素数量、属性字段、样例数据与经纬度范围；调用其他工具前应先确认字段名', DatasetIdInput),
    _spec(
        'aggregate_points_by_region',
        '统计每个面对象内包含的点数量。pointsId 为点数据集，polygonsId 为面数据集，groupField 为面数据上用于分组的字段（如 name）',
        AggregateInput,
    ),
    _spec(
        'render_scene',
        '生成或增量更新三维场景。图层 kind：polygon-extrusion（按指标拉伸的三维柱）、polygon-fill（平面填色）、points（点位）、gltf-model（在点名位置挂载 glb 模型，需配 modelId/nameValue）。'
        '指标经 metricValues 传入（需配 metricGroupField/metricName），坐标由服务端解析；cameraDatasetId 指定视角对准的数据集；'
        'basemap 可选 gaode/osm/arcgis-satellite；terrain 可选 arcgis（在线地形）/flat。'
        '多轮默认 merge：同 id 图层替换、新图层追加、未提及的图层与底图/地形保留；用户明确要求清空或全部换成新内容时才用 replaceScene=true',
        RenderSceneInput,
    ),
    _spec('fly_to', '把三维视角飞行定位到指定数据集的范围中心', DatasetIdInput),
    _spec(
        'search_knowledge',
        '检索 GeoMind 知识库：空间分析方法（密度归一化/缓冲区/空间连接）、场景参数选择（heightScale/配色/pointSize）、工具语义（merge/replaceScene/指标注入）、常见错误自纠。'
        '遇到不确定分析方法或参数时先检索；任务明确时不要无谓调用',
        SearchKnowledgeInput,
    ),
]
