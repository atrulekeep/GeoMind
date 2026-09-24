"""场景导出：生成自包含 HTML

一个 HTML 文件 = Cesium CDN + 内嵌 SceneSpec JSON + 渲染脚本。
双击打开即渲染三维场景，无需安装任何环境。
暴露 window.__GEOMIND_SPEC__ 和 window.__GEOMIND_RENDER__ 支持二次开发。
"""

import json
from typing import Any

CESIUM_CDN_CSS = 'https://cdn.jsdelivr.net/npm/cesium@1.145.0/Build/Cesium/Widgets/widgets.css'
CESIUM_CDN_JS = 'https://cdn.jsdelivr.net/npm/cesium@1.145.0/Build/Cesium/Cesium.js'

HTML_TEMPLATE = """<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>GeoMind 场景导出</title>
<link rel="stylesheet" href="{css_cdn}">
<style>
  * {{ margin:0; padding:0; box-sizing:border-box; }}
  html,body {{ width:100%; height:100%; overflow:hidden; background:#0b1020; }}
  #cesiumContainer {{ width:100%; height:100%; }}
  .info-bar {{
    position:absolute; top:12px; left:12px; z-index:9999;
    background:rgba(13,20,38,0.92); border:1px solid #232f4a; border-radius:8px;
    padding:10px 16px; color:#d6e1f5; font:13px/1.6 -apple-system,sans-serif;
    max-width:360px; backdrop-filter:blur(8px); display:none;
  }}
  .info-toggle {{
    position:absolute; top:12px; left:12px; z-index:9999;
    width:36px; height:36px; border-radius:50%;
    background:rgba(13,20,38,0.92); border:1px solid #232f4a;
    color:#9fc0ff; font-size:17px; cursor:pointer;
    display:flex; align-items:center; justify-content:center;
    backdrop-filter:blur(8px); user-select:none;
    font-family:-apple-system,sans-serif;
  }}
  .info-toggle:hover {{ border-color:#3b82f6; }}
  .info-bar .close-x {{
    position:absolute; top:6px; right:9px; cursor:pointer;
    color:#7f8db0; font-size:12px; user-select:none;
  }}
  .info-bar .close-x:hover {{ color:#d6e1f5; }}
  .info-bar h3 {{ font-size:14px; margin-bottom:4px; color:#9fc0ff; }}
  .info-bar .meta {{ font-size:11px; color:#7f8db0; margin-top:6px; }}
  .info-bar code {{ background:#1a2440; padding:1px 5px; border-radius:3px; font-size:11px; color:#4ade80; }}
  .file-guard {{
    position:fixed; inset:0; z-index:10000; display:none;
    align-items:center; justify-content:center; background:rgba(11,16,32,0.96);
  }}
  .guard-card {{
    max-width:520px; background:#0d1426; border:1px solid #2c3c60; border-radius:12px;
    padding:28px 32px; color:#d6e1f5; font:14px/1.8 -apple-system,sans-serif;
  }}
  .guard-card h2 {{ font-size:17px; color:#fbbf24; margin-bottom:12px; }}
  .guard-card code {{
    display:block; background:#1a2440; border:1px solid #2c3c60; border-radius:6px;
    padding:8px 12px; margin:8px 0; font-size:13px; color:#4ade80;
    user-select:all; word-break:break-all;
  }}
  .guard-card .alt {{ font-size:12px; color:#7f8db0; margin-top:14px; }}
</style>
</head>
<body>
<div id="fileGuard" class="file-guard">
  <div class="guard-card">
    <h2>浏览器安全限制：file:// 无法加载三维引擎</h2>
    <p>Chrome 禁止 file:// 页面创建 Web Worker（Cesium 必需），需要通过 HTTP 访问本文件。</p>
    <p><b>方法一</b>：在终端进入本文件所在目录，执行：</p>
    <code>python3 -m http.server 8899</code>
    <p>然后浏览器打开（替换为实际文件名）：</p>
    <code>http://localhost:8899/本文件名.html</code>
    <p class="alt">方法二：联系分享者获取在线预览链接（GeoMind sidecar 运行时，http://127.0.0.1:8765/api/scene/export 生成）。</p>
  </div>
</div>
<div id="cesiumContainer"></div>
<div id="infoToggle" class="info-toggle" title="场景信息与二次开发说明">ℹ</div>
<div id="infoBar" class="info-bar">
  <span class="close-x" id="infoClose" title="收起">✕</span>
  <h3>GeoMind 导出场景</h3>
  <div id="sceneInfo">渲染中…</div>
  <div class="meta">
    二次开发：打开控制台，修改 <code>window.__GEOMIND_SPEC__</code> 后调用 <code>window.__GEOMIND_RENDER__(window.__GEOMIND_SPEC__)</code>
  </div>
</div>
<script src="{js_cdn}"></script>
<script>
window.__GEOMIND_SPEC__ = {spec_json};

// 渲染逻辑（从 sceneRenderer.ts 提取的独立版本，不依赖构建工具）
(function() {{
  // file:// 页面是唯一安全源，跨域 Worker 被禁，Cesium 无法工作——显示引导而非白屏报错
  if (location.protocol === 'file:') {{
    document.getElementById('fileGuard').style.display = 'flex';
    return;
  }}

  // 左上角信息面板：默认隐藏，点击图标展开，✕ 收起
  var infoToggle = document.getElementById('infoToggle');
  var infoBar = document.getElementById('infoBar');
  infoToggle.addEventListener('click', function() {{
    infoBar.style.display = 'block';
    infoToggle.style.display = 'none';
  }});
  document.getElementById('infoClose').addEventListener('click', function() {{
    infoBar.style.display = 'none';
    infoToggle.style.display = 'flex';
  }});
  var ARCGIS_TERRAIN_URL = 'https://elevation3d.arcgis.com/arcgis/rest/services/WorldElevation3D/Terrain3D/ImageServer';

  function parseColor(hex) {{
    var d = '#3FA7E6CC';
    if (!hex) hex = d;
    if (/^#?[0-9a-fA-F]{{8}}$/.test(hex)) {{
      var h = hex.replace('#','');
      var rgb = h.slice(0,6), a = parseInt(h.slice(6,8),16)/255;
      return Cesium.Color.fromCssColorString('#'+rgb).withAlpha(a);
    }}
    return Cesium.Color.fromCssColorString(hex);
  }}

  function createImageryProvider(id) {{
    if (id === 'osm')
      return new Cesium.OpenStreetMapImageryProvider({{ url:'https://tile.openstreetmap.org/' }});
    if (id === 'arcgis-satellite')
      return new Cesium.UrlTemplateImageryProvider({{
        url:'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{{z}}/{{y}}/{{x}}',
        maximumLevel:18
      }});
    return new Cesium.UrlTemplateImageryProvider({{
      url:'https://webrd0{{s}}.is.autonavi.com/appmaptile?lang=zh_cn&size=1&scale=1&style=8&x={{x}}&y={{y}}&z={{z}}',
      subdomains:['1','2','3','4'], maximumLevel:18
    }});
  }}

  var viewer = new Cesium.Viewer('cesiumContainer', {{
    baseLayer: new Cesium.ImageryLayer(
      createImageryProvider(window.__GEOMIND_SPEC__.basemap || 'gaode')
    ),
    baseLayerPicker:false, animation:false, timeline:false,
    navigationHelpButton:false, sceneModePicker:false, homeButton:false,
    geocoder:false, infoBox:false, selectionIndicator:false
  }});
  viewer.scene.globe.baseColor = Cesium.Color.fromCssColorString('#111a33');
  viewer.camera.setView({{
    destination: Cesium.Cartesian3.fromDegrees(116.4, 39.9, 1800000)
  }});
  viewer.cesiumWidget.creditContainer.style.display = 'none';

  // 地形：与主程序一致的模式（ArcGIS 免 token，失败不阻断渲染）
  if (window.__GEOMIND_SPEC__.terrain === 'arcgis') {{
    Cesium.ArcGISTiledElevationTerrainProvider.fromUrl(ARCGIS_TERRAIN_URL)
      .then(function(provider) {{
        viewer.scene.setTerrain(new Cesium.Terrain(Promise.resolve(provider)));
      }})
      .catch(function(e) {{ console.warn('[GeoMind] terrain load failed:', e); }});
  }}

  function renderPoints(layer) {{
    var color = parseColor(layer.color);
    (layer.geojson ? layer.geojson.features : []).forEach(function(f) {{
      if (!f.geometry || f.geometry.type !== 'Point') return;
      var c = f.geometry.coordinates;
      viewer.entities.add({{
        position: Cesium.Cartesian3.fromDegrees(c[0], c[1]),
        point: {{
          pixelSize: layer.pointSize || 8, color: color,
          outlineColor: color.withAlpha(0.4), outlineWidth: 1,
          disableDepthTestDistance: Number.POSITIVE_INFINITY
        }}
      }});
    }});
  }}

  function renderModels(layer) {{
    if (!layer.url || !layer.position) return;
    var pos = Cesium.Cartesian3.fromDegrees(
      layer.position.lon, layer.position.lat, layer.position.height || 0
    );
    var hpr = new Cesium.HeadingPitchRoll(
      Cesium.Math.toRadians(layer.headingDegrees || 0), 0, 0
    );
    viewer.entities.add({{
      position: pos,
      orientation: Cesium.Transforms.headingPitchRollQuaternion(pos, hpr),
      model: {{ uri: layer.url, scale: layer.scale || 1, minimumPixelSize: 32 }}
    }});
  }}

  var labelItems = [];

  function renderLabels(layer) {{
    var color = parseColor(layer.color || '#FFFFFF');
    var fields = layer.labelFields || [];
    var fontSize = layer.labelFontSize || 14;
    var items = [];
    (layer.geojson ? layer.geojson.features : []).forEach(function(f) {{
      if (!f.geometry || f.geometry.type !== 'Point') return;
      var c = f.geometry.coordinates;
      var props = f.properties || {{}};
      var text = fields
        .map(function(name) {{
          var v = props[name];
          return typeof v === 'number' ? String(Number(v.toFixed(2))) : String(v == null ? '' : v).trim();
        }})
        .filter(Boolean)
        .join(' ');
      var full = [text, String(layer.labelUnit || '').trim()].filter(Boolean).join(' ');
      if (!full) return;
      var raw = layer.heightProperty ? Number(props[layer.heightProperty] || 0) : 0;
      var height = (Number.isFinite(raw) ? raw : 0) * (layer.heightScale || 1);
      var pos = Cesium.Cartesian3.fromDegrees(c[0], c[1], height);
      var ent = viewer.entities.add({{
        position: pos,
        label: {{
          text: full,
          font: 'bold ' + fontSize + 'px "PingFang SC", "Microsoft YaHei", sans-serif',
          fillColor: color,
          outlineColor: Cesium.Color.BLACK.withAlpha(0.85),
          outlineWidth: 2,
          style: Cesium.LabelStyle.FILL_AND_OUTLINE,
          showBackground: true,
          backgroundColor: Cesium.Color.fromCssColorString('#1F2937').withAlpha(0.72),
          backgroundPadding: new Cesium.Cartesian2(7, 4),
          verticalOrigin: Cesium.VerticalOrigin.BOTTOM,
          disableDepthTestDistance: Number.POSITIVE_INFINITY
        }}
      }});
      items.push({{
        entity: ent, pos: pos,
        w: full.length * fontSize * 0.62 + 16, h: fontSize + 12,
        priority: Number.isFinite(raw) ? raw : 0
      }});
    }});
    labelItems = labelItems.filter(function(it) {{ return it.layerId !== layer.id; }});
    items.forEach(function(it) {{ it.layerId = layer.id; labelItems.push(it); }});
  }}

  function polygonRings(geometry) {{
    if (!geometry) return [];
    if (geometry.type === 'Polygon') return geometry.coordinates;
    if (geometry.type === 'MultiPolygon') return geometry.coordinates.flat();
    return [];
  }}

  function renderOutline(layer) {{
    var color = parseColor(layer.color);
    var width = layer.width || 3;
    var clamped = window.__GEOMIND_SPEC__.terrain === 'arcgis';
    (layer.geojson ? layer.geojson.features : []).forEach(function(f) {{
      polygonRings(f.geometry).forEach(function(ring) {{
        if (!ring || ring.length < 3) return;
        var flat = [];
        ring.forEach(function(c) {{
          flat.push(c[0], c[1]);
          if (!clamped) flat.push(2);
        }});
        viewer.entities.add({{
          polyline: {{
            positions: clamped
              ? Cesium.Cartesian3.fromDegreesArray(flat)
              : Cesium.Cartesian3.fromDegreesArrayHeights(flat),
            width: width,
            material: color,
            clampToGround: clamped
          }}
        }});
      }});
    }});
  }}

  // 标注屏幕避让：投影到屏幕坐标做矩形相交，重叠时隐藏指标值小的
  function updateLabelVisibility() {{
    if (labelItems.length === 0) return;
    var sorted = labelItems.slice().sort(function(a, b) {{ return b.priority - a.priority; }});
    var placed = [];
    sorted.forEach(function(item) {{
      var show = false;
      var wc = Cesium.SceneTransforms.worldToWindowCoordinates(viewer.scene, item.pos);
      if (wc) {{
        var rect = {{ x: wc.x - item.w / 2, y: wc.y - item.h, w: item.w, h: item.h }};
        var overlap = placed.some(function(r) {{
          return rect.x < r.x + r.w && rect.x + rect.w > r.x &&
                 rect.y < r.y + r.h && rect.y + rect.h > r.y;
        }});
        if (!overlap) {{ placed.push(rect); show = true; }}
      }}
      if (item.entity.show !== show) item.entity.show = show;
    }});
  }}
  viewer.camera.percentageChanged = 0.02;
  viewer.camera.changed.addEventListener(updateLabelVisibility);

  async function renderPolygons(layer) {{
    if (!layer.geojson) return;
    var ds = await Cesium.GeoJsonDataSource.load(layer.geojson, {{
      clampToGround: layer.kind !== 'polygon-extrusion'
    }});
    var isExtr = layer.kind === 'polygon-extrusion';
    var color = parseColor(layer.color);
    var entities = ds.entities.values;
    layer.geojson.features.forEach(function(f, i) {{
      var ent = entities[i];
      if (!ent || !ent.polygon) return;
      var props = f.properties || {{}};
      if (isExtr && layer.heightProperty) {{
        var raw = Number(props[layer.heightProperty] || 0);
        var h = (Number.isFinite(raw) ? raw : 0) * (layer.heightScale || 1);
        ent.polygon.height = new Cesium.ConstantProperty(0);
        ent.polygon.extrudedHeight = new Cesium.ConstantProperty(h);
        ent.polygon.material = new Cesium.ColorMaterialProperty(color.withAlpha(0.9));
        ent.polygon.outline = new Cesium.ConstantProperty(true);
        ent.polygon.outlineColor = new Cesium.ConstantProperty(color.withAlpha(0.4));
      }} else {{
        ent.polygon.material = new Cesium.ColorMaterialProperty(color.withAlpha(0.7));
      }}
    }});
    viewer.dataSources.add(ds);
  }}

  async function renderScene(spec) {{
    viewer.entities.removeAll();
    viewer.dataSources.removeAll(true);
    if (spec.basemap) {{
      viewer.imageryLayers.removeAll();
      viewer.imageryLayers.addImageryProvider(createImageryProvider(spec.basemap));
    }}
    for (var i = 0; i < spec.layers.length; i++) {{
      var layer = spec.layers[i];
      if (layer.kind === 'points') renderPoints(layer);
      else if (layer.kind === 'gltf-model') renderModels(layer);
      else if (layer.kind === 'labels') renderLabels(layer);
      else if (layer.kind === 'polygon-outline') renderOutline(layer);
      else await renderPolygons(layer);
    }}
    if (spec.camera) {{
      viewer.camera.flyTo({{
        destination: Cesium.Cartesian3.fromDegrees(
          spec.camera.lon, spec.camera.lat, spec.camera.height
        ),
        orientation: {{
          heading: spec.camera.heading || 0,
          pitch: spec.camera.pitch != null ? spec.camera.pitch : Cesium.Math.toRadians(-45),
          roll: 0
        }},
        duration: 1.2
      }});
    }}
  }}

  window.__GEOMIND_RENDER__ = renderScene;

  // 初始渲染
  renderScene(window.__GEOMIND_SPEC__).then(function() {{
    updateLabelVisibility();
    var s = window.__GEOMIND_SPEC__;
    var info = document.getElementById('sceneInfo');
    var layerDescs = s.layers.map(function(l) {{
      return l.kind + ' (' + l.id + ')' +
        (l.geojson ? ' · ' + l.geojson.features.length + ' 要素' : '');
    }});
    info.innerHTML =
      '<b>' + s.layers.length + ' 个图层</b><br>' +
      layerDescs.join('<br>') +
      (s.basemap ? '<br>底图: ' + s.basemap : '') +
      (s.terrain ? ' · 地形: ' + s.terrain : '');
  }});
}})();
</script>
</body>
</html>"""


def generate_export_html(scene_spec: dict[str, Any]) -> str:
    """生成自包含 HTML 字符串"""
    spec_json = json.dumps(scene_spec, ensure_ascii=False, indent=2)
    return HTML_TEMPLATE.format(
        css_cdn=CESIUM_CDN_CSS,
        js_cdn=CESIUM_CDN_JS,
        spec_json=spec_json,
    )
