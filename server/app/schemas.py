"""SceneSpec 协议（Python 侧）

分为两层：
- *Input：LLM 调用 render_scene 时提交的"以数据集为引用"的描述（不含坐标）
- *Resolved：服务端解析后内嵌 GeoJSON、可直接交给 Cesium 渲染的完整场景
"""

from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

LayerKind = Literal[
    'polygon-fill', 'polygon-extrusion', 'points', 'gltf-model', 'labels', 'polygon-outline'
]
BasemapId = Literal['gaode', 'osm', 'arcgis-satellite']
TerrainId = Literal['flat', 'arcgis']


class StrictModel(BaseModel):
    """LLM 入参一律禁止未声明字段：模型臆造参数时立即报错回喂，而不是静默忽略"""

    model_config = ConfigDict(extra='forbid')


class MetricValue(StrictModel):
    group: str
    value: float


class SceneLayerInput(StrictModel):
    id: str = Field(min_length=1, max_length=64)
    kind: LayerKind
    # 矢量图层：数据集引用；模型图层：锚点点数据集引用
    datasetId: str
    # 未指定时按图层类型取默认：labels 白字（配深色底衬），其余 #3FA7E6CC
    color: Optional[str] = None
    # 指标注入：按 metricGroupField 匹配数据集要素，写入 metricName 属性
    metricGroupField: Optional[str] = None
    metricName: Optional[str] = 'metric'
    metricValues: Optional[list[MetricValue]] = None
    # 直读数据集自带指标字段（与 metricValues 二选一）；render_scene 返回 metricSummary 供引用真实数值
    metricField: Optional[str] = None
    # 矢量渲染参数
    heightScale: Optional[float] = 1
    pointSize: Optional[int] = 8
    # labels 专用：显示的字段列表（逐要素取值拼接为文字），labelFontSize 为字号像素，
    # labelUnit 为附加在文字末尾的单位（如 '亿元' → '东城区 3574.3 亿元'）
    labelFields: Optional[list[str]] = None
    labelFontSize: Optional[int] = 14
    labelUnit: Optional[str] = None
    # polygon-outline 专用：边界线宽（像素）
    width: Optional[float] = 3
    # gltf-model 专用：modelId 见模型注册表，位置按 datasetId 中
    # nameField == nameValue 的点要素解析（LLM 不接触坐标）
    modelId: Optional[str] = None
    nameField: Optional[str] = 'name'
    nameValue: Optional[str] = None
    scale: Optional[float] = None
    heightOffset: Optional[float] = 0
    headingDegrees: Optional[float] = 0


class RenderSceneInput(StrictModel):
    layers: list[SceneLayerInput] = Field(min_length=1)
    cameraDatasetId: Optional[str] = None
    # gaode=高德矢量(默认)；osm=OpenStreetMap；arcgis-satellite=ArcGIS 卫星影像
    basemap: Optional[BasemapId] = None
    # arcgis=ArcGIS 在线地形(免 token)；flat=椭球平面
    terrain: Optional[TerrainId] = None
    # 默认 merge：同 id 图层替换、新图层追加、其余保留；true 时清空旧场景全量重建
    replaceScene: bool = False


class DatasetIdInput(StrictModel):
    datasetId: str


class AggregateInput(StrictModel):
    pointsId: str
    polygonsId: str
    groupField: str


class SearchKnowledgeInput(StrictModel):
    query: str = Field(min_length=2, max_length=200)
    k: int = Field(default=4, ge=1, le=8)


# ---------- Resolved ----------


class ModelPosition(BaseModel):
    lon: float
    lat: float
    height: float = 0


class ResolvedSceneLayer(BaseModel):
    id: str
    kind: LayerKind
    # gltf-model 图层不内嵌 geojson；矢量图层必有
    geojson: Optional[dict[str, Any]] = None
    color: Optional[str] = None
    heightProperty: Optional[str] = None
    heightScale: Optional[float] = None
    pointSize: Optional[int] = None
    # labels
    labelFields: Optional[list[str]] = None
    labelFontSize: Optional[int] = None
    labelUnit: Optional[str] = None
    # polygon-outline
    width: Optional[float] = None
    # gltf-model
    url: Optional[str] = None
    position: Optional[ModelPosition] = None
    scale: Optional[float] = None
    headingDegrees: Optional[float] = None


class ResolvedCamera(BaseModel):
    lon: float
    lat: float
    height: float
    heading: float = 0
    pitch: float = -0.785


class SceneSpec(BaseModel):
    version: int = 1
    layers: list[ResolvedSceneLayer]
    camera: Optional[ResolvedCamera] = None
    basemap: Optional[str] = None
    terrain: Optional[str] = None
