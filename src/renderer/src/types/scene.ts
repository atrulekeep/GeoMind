/**
 * SceneSpec：Agent 与三维渲染端之间的声明式场景协议
 * Agent 只产出协议，不直接生成绘图代码；多轮对话 = 对 SceneSpec 的迭代
 */

export interface GeoJsonFeature {
  type: 'Feature'
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  geometry: { type: string; coordinates: any }
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  properties: Record<string, any> | null
}

export interface FeatureCollection {
  type: 'FeatureCollection'
  features: GeoJsonFeature[]
}

export type LayerKind =
  | 'polygon-fill'
  | 'polygon-extrusion'
  | 'points'
  | 'gltf-model'
  | 'labels'
  | 'polygon-outline'
export type BasemapId = 'gaode' | 'osm' | 'arcgis-satellite'
export type TerrainId = 'flat' | 'arcgis'

export interface ModelPosition {
  lon: number
  lat: number
  height?: number
}

export interface SceneLayer {
  id: string
  kind: LayerKind
  /** 矢量图层必有：协议中的数据始终内嵌，渲染端无需再请求后端（便于回放/导出） */
  geojson?: FeatureCollection
  /** #RRGGBBAA 或 #RRGGBB */
  color?: string
  /** 拉伸体：高度取自要素该属性值 × heightScale */
  heightProperty?: string
  heightScale?: number
  pointSize?: number
  /** labels：显示的字段列表（逐要素取值拼接），labelFontSize 为字号像素，labelUnit 追加在文字末尾 */
  labelFields?: string[]
  labelFontSize?: number
  labelUnit?: string
  /** polygon-outline：边界线宽（像素） */
  width?: number
  /** gltf-model 图层 */
  url?: string
  position?: ModelPosition
  scale?: number
  headingDegrees?: number
}

export interface CameraSpec {
  lon: number
  lat: number
  height: number
  heading?: number
  pitch?: number
}

export interface SceneSpec {
  version: 1
  layers: SceneLayer[]
  camera?: CameraSpec
  basemap?: BasemapId
  terrain?: TerrainId
}
