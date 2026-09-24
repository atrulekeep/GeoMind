import './cesium-base'
import * as Cesium from 'cesium'
import 'cesium/Build/Cesium/Widgets/widgets.css'
import type {
  BasemapId,
  GeoJsonFeature,
  SceneLayer,
  SceneSpec,
  TerrainId
} from '@renderer/types/scene'

const DEFAULT_COLOR = '#3FA7E6CC'

const ARCGIS_TERRAIN_URL =
  'https://elevation3d.arcgis.com/arcgis/rest/services/WorldElevation3D/Terrain3D/ImageServer'

function parseColor(hex: string | undefined): Cesium.Color {
  if (!hex) return Cesium.Color.fromCssColorString(DEFAULT_COLOR)
  if (/^#?[0-9a-fA-F]{8}$/.test(hex)) {
    const h = hex.replace('#', '')
    const rgb = h.slice(0, 6)
    const a = parseInt(h.slice(6, 8), 16) / 255
    return Cesium.Color.fromCssColorString(`#${rgb}`).withAlpha(a)
  }
  return Cesium.Color.fromCssColorString(hex)
}

function pointCoords(feature: GeoJsonFeature): [number, number] | null {
  const c = feature.geometry.coordinates as unknown
  if (feature.geometry.type === 'Point' && Array.isArray(c)) {
    return [c[0] as number, c[1] as number]
  }
  return null
}

/** 提取 Polygon/MultiPolygon 的所有外环（[lon, lat] 顶点数组）用于画边界线 */
function polygonRings(geometry: GeoJsonFeature['geometry']): number[][][] {
  if (geometry.type === 'Polygon') return geometry.coordinates as number[][][]
  if (geometry.type === 'MultiPolygon') return (geometry.coordinates as number[][][][]).flat()
  return []
}

function formatLabelValue(value: unknown): string {
  if (typeof value === 'number') return String(Number(value.toFixed(2)))
  return String(value ?? '').trim()
}

/** 参与屏幕避让的标注条目 */
interface LabelItem {
  entity: Cesium.Entity
  pos: Cesium.Cartesian3
  /** 近似屏幕占用（像素） */
  w: number
  h: number
  /** 遮挡优先级：指标值大者优先显示 */
  priority: number
}

function createImageryProvider(id: BasemapId): Cesium.ImageryProvider {
  if (id === 'osm') {
    return new Cesium.OpenStreetMapImageryProvider({ url: 'https://tile.openstreetmap.org/' })
  }
  if (id === 'arcgis-satellite') {
    return new Cesium.UrlTemplateImageryProvider({
      url: 'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',
      maximumLevel: 18
    })
  }
  return new Cesium.UrlTemplateImageryProvider({
    url: 'https://webrd0{s}.is.autonavi.com/appmaptile?lang=zh_cn&size=1&scale=1&style=8&x={x}&y={y}&z={z}',
    subdomains: ['1', '2', '3', '4'],
    maximumLevel: 18
  })
}

/** 把 SceneSpec 应用到 Cesium Viewer；同一 layer.id 做替换，其余清除 */
export class SceneRenderer {
  private dataSources = new Map<string, Cesium.DataSource>()
  private pointEntities = new Map<string, Cesium.Entity[]>()
  private modelEntities = new Map<string, Cesium.Entity[]>()
  private labelEntities = new Map<string, Cesium.Entity[]>()
  private outlineEntities = new Map<string, Cesium.Entity[]>()
  private labelItems = new Map<string, LabelItem[]>()
  // Viewer 初始底图是高德；未指定 basemap 的场景不改变现状
  private currentBasemap: BasemapId = 'gaode'
  private currentTerrain: TerrainId = 'flat'

  constructor(private readonly viewer: Cesium.Viewer) {
    // 相机停止变化时重算标注的屏幕占位，实现缩放/旋转下的自动避让
    viewer.camera.percentageChanged = 0.02
    viewer.camera.changed.addEventListener(() => this.updateLabelVisibility())
  }

  async render(scene: SceneSpec): Promise<void> {
    if (scene.basemap) this.applyBasemap(scene.basemap)
    if (scene.terrain) await this.applyTerrain(scene.terrain)

    const keep = new Set<string>()
    for (const layer of scene.layers) {
      keep.add(layer.id)
      if (layer.kind === 'points') {
        this.renderPoints(layer)
      } else if (layer.kind === 'gltf-model') {
        this.renderModels(layer)
      } else if (layer.kind === 'labels') {
        this.renderLabels(layer)
      } else if (layer.kind === 'polygon-outline') {
        this.renderOutline(layer)
      } else {
        await this.renderPolygons(layer)
      }
    }
    for (const id of [...this.dataSources.keys()]) {
      if (!keep.has(id)) {
        const ds = this.dataSources.get(id)
        if (ds) this.viewer.dataSources.remove(ds, true)
        this.dataSources.delete(id)
      }
    }
    for (const map of [
      this.pointEntities,
      this.modelEntities,
      this.labelEntities,
      this.outlineEntities
    ] as const) {
      for (const id of [...map.keys()]) {
        if (!keep.has(id)) {
          for (const e of map.get(id) ?? []) this.viewer.entities.remove(e)
          map.delete(id)
        }
      }
    }
    for (const id of [...this.labelItems.keys()]) {
      if (!keep.has(id)) this.labelItems.delete(id)
    }
    this.updateLabelVisibility()
    if (scene.camera) {
      const cam = scene.camera
      this.viewer.camera.flyTo({
        destination: Cesium.Cartesian3.fromDegrees(cam.lon, cam.lat, cam.height),
        orientation: {
          heading: cam.heading ?? 0,
          pitch: cam.pitch ?? Cesium.Math.toRadians(-45),
          roll: 0
        },
        duration: 1.2
      })
    }
  }

  private applyBasemap(id: BasemapId): void {
    if (id === this.currentBasemap) return
    this.viewer.imageryLayers.removeAll()
    this.viewer.imageryLayers.addImageryProvider(createImageryProvider(id))
    this.currentBasemap = id
  }

  private async applyTerrain(id: TerrainId): Promise<void> {
    if (id === this.currentTerrain) return
    try {
      if (id === 'arcgis') {
        const provider =
          await Cesium.ArcGISTiledElevationTerrainProvider.fromUrl(ARCGIS_TERRAIN_URL)
        this.viewer.scene.setTerrain(new Cesium.Terrain(Promise.resolve(provider)))
      } else {
        this.viewer.scene.setTerrain(
          new Cesium.Terrain(Promise.resolve(new Cesium.EllipsoidTerrainProvider()))
        )
      }
      this.currentTerrain = id
    } catch (err) {
      // 地形是增强项，在线服务不可用时不应阻断场景渲染
      console.warn('[GeoMind] terrain switch failed:', err)
    }
  }

  private async renderPolygons(layer: SceneLayer): Promise<void> {
    if (!layer.geojson) return
    const old = this.dataSources.get(layer.id)
    if (old) {
      this.viewer.dataSources.remove(old, true)
      this.dataSources.delete(layer.id)
    }

    const isExtrusion = layer.kind === 'polygon-extrusion'
    const color = parseColor(layer.color)

    const ds = await Cesium.GeoJsonDataSource.load(layer.geojson, {
      clampToGround: !isExtrusion
    })

    const entities = ds.entities.values
    layer.geojson?.features.forEach((feature, i) => {
      const entity = entities[i]
      if (!entity?.polygon) return
      const props = feature.properties ?? {}
      if (isExtrusion && layer.heightProperty) {
        const raw = Number(props[layer.heightProperty] ?? 0)
        const value = Number.isFinite(raw) ? raw : 0
        const h = value * (layer.heightScale ?? 1)
        entity.polygon.height = new Cesium.ConstantProperty(0)
        entity.polygon.extrudedHeight = new Cesium.ConstantProperty(h)
        entity.polygon.material = new Cesium.ColorMaterialProperty(color.withAlpha(0.9))
        entity.polygon.outline = new Cesium.ConstantProperty(true)
        entity.polygon.outlineColor = new Cesium.ConstantProperty(color.withAlpha(0.4))
      } else {
        entity.polygon.material = new Cesium.ColorMaterialProperty(color.withAlpha(0.7))
      }
    })

    await this.viewer.dataSources.add(ds)
    this.dataSources.set(layer.id, ds)
  }

  private renderPoints(layer: SceneLayer): void {
    for (const e of this.pointEntities.get(layer.id) ?? []) this.viewer.entities.remove(e)
    const color = parseColor(layer.color)
    const added: Cesium.Entity[] = []
    for (const feature of layer.geojson?.features ?? []) {
      const coords = pointCoords(feature)
      if (!coords) continue
      added.push(
        this.viewer.entities.add({
          position: Cesium.Cartesian3.fromDegrees(coords[0], coords[1]),
          point: {
            pixelSize: layer.pointSize ?? 8,
            color,
            outlineColor: color.withAlpha(0.4),
            outlineWidth: 1,
            disableDepthTestDistance: Number.POSITIVE_INFINITY
          }
        })
      )
    }
    this.pointEntities.set(layer.id, added)
  }

  /** labels：文字标注（服务端已把面要素锚点解析为面内代表点）；经典样式=白字+深色底衬+黑描边 */
  private renderLabels(layer: SceneLayer): void {
    for (const e of this.labelEntities.get(layer.id) ?? []) this.viewer.entities.remove(e)
    const color = parseColor(layer.color || '#FFFFFF')
    const fields = layer.labelFields ?? []
    const fontSize = layer.labelFontSize ?? 14
    const items: LabelItem[] = []
    for (const feature of layer.geojson?.features ?? []) {
      const coords = pointCoords(feature)
      if (!coords) continue
      const props = feature.properties ?? {}
      const text = fields
        .map((f) => formatLabelValue(props[f]))
        .filter(Boolean)
        .join(' ')
      const full = [text, layer.labelUnit?.trim()].filter(Boolean).join(' ')
      if (!full) continue
      // 与柱图层同指标来源 + 同 heightScale 时，文字自动升到柱顶
      const raw = layer.heightProperty ? Number(props[layer.heightProperty] ?? 0) : 0
      const height = (Number.isFinite(raw) ? raw : 0) * (layer.heightScale ?? 1)
      const pos = Cesium.Cartesian3.fromDegrees(coords[0], coords[1], height)
      const entity = this.viewer.entities.add({
        position: pos,
        label: {
          text: full,
          font: `bold ${fontSize}px "PingFang SC", "Microsoft YaHei", sans-serif`,
          fillColor: color,
          outlineColor: Cesium.Color.BLACK.withAlpha(0.85),
          outlineWidth: 2,
          style: Cesium.LabelStyle.FILL_AND_OUTLINE,
          showBackground: true,
          backgroundColor: Cesium.Color.fromCssColorString('#1F2937').withAlpha(0.72),
          backgroundPadding: new Cesium.Cartesian2(7, 4),
          verticalOrigin: Cesium.VerticalOrigin.BOTTOM,
          disableDepthTestDistance: Number.POSITIVE_INFINITY
        }
      })
      items.push({
        entity,
        pos,
        // 近似屏幕占位：字符宽 × 0.62 + 底衬内边距
        w: full.length * fontSize * 0.62 + 16,
        h: fontSize + 12,
        // 指标值大的标注优先显示（如 GDP 高的区在重叠时保留）
        priority: Number.isFinite(raw) ? raw : 0
      })
    }
    this.labelEntities.set(
      layer.id,
      items.map((i) => i.entity)
    )
    this.labelItems.set(layer.id, items)
  }

  /** polygon-outline：区域边界线，用于区分相邻区域。
   * flat 地形下抬升 2m 绘制——贴地线与贴地填色面存在深度冲突，内部边界会被面盖住 */
  private renderOutline(layer: SceneLayer): void {
    for (const e of this.outlineEntities.get(layer.id) ?? []) this.viewer.entities.remove(e)
    const color = parseColor(layer.color)
    const width = layer.width ?? 3
    const clamped = this.currentTerrain === 'arcgis'
    const added: Cesium.Entity[] = []
    for (const feature of layer.geojson?.features ?? []) {
      for (const ring of polygonRings(feature.geometry)) {
        if (ring.length < 3) continue
        const flat: number[] = []
        for (const c of ring) {
          flat.push(c[0], c[1])
          if (!clamped) flat.push(2)
        }
        added.push(
          this.viewer.entities.add({
            polyline: {
              positions: clamped
                ? Cesium.Cartesian3.fromDegreesArray(flat)
                : Cesium.Cartesian3.fromDegreesArrayHeights(flat),
              width,
              material: color,
              clampToGround: clamped
            }
          })
        )
      }
    }
    this.outlineEntities.set(layer.id, added)
  }

  /** 标注屏幕避让：把各标注投影到屏幕坐标做矩形相交测试，重叠时隐藏优先级低（指标值小）的 */
  private updateLabelVisibility(): void {
    const all: LabelItem[] = []
    for (const items of this.labelItems.values()) all.push(...items)
    if (all.length === 0) return
    all.sort((a, b) => b.priority - a.priority)
    const placed: { x: number; y: number; w: number; h: number }[] = []
    for (const item of all) {
      let show = false
      const wc = Cesium.SceneTransforms.worldToWindowCoordinates(this.viewer.scene, item.pos)
      if (wc) {
        const rect = { x: wc.x - item.w / 2, y: wc.y - item.h, w: item.w, h: item.h }
        const overlap = placed.some(
          (r) =>
            rect.x < r.x + r.w &&
            rect.x + rect.w > r.x &&
            rect.y < r.y + r.h &&
            rect.y + rect.h > r.y
        )
        if (!overlap) {
          placed.push(rect)
          show = true
        }
      }
      if (item.entity.show !== show) item.entity.show = show
    }
  }

  private renderModels(layer: SceneLayer): void {
    for (const e of this.modelEntities.get(layer.id) ?? []) this.viewer.entities.remove(e)
    if (!layer.url || !layer.position) return

    const position = Cesium.Cartesian3.fromDegrees(
      layer.position.lon,
      layer.position.lat,
      layer.position.height ?? 0
    )
    const heading = Cesium.Math.toRadians(layer.headingDegrees ?? 0)
    const hpr = new Cesium.HeadingPitchRoll(heading, 0, 0)
    const orientation = Cesium.Transforms.headingPitchRollQuaternion(position, hpr)

    const entity = this.viewer.entities.add({
      position,
      orientation,
      model: {
        uri: layer.url,
        scale: layer.scale ?? 1,
        minimumPixelSize: 32
      }
    })
    this.modelEntities.set(layer.id, [entity])
  }
}
