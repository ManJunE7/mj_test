import os
import math
import requests
import pandas as pd
import geopandas as gpd
import streamlit as st
import folium
from shapely.geometry import Point
from folium.plugins import MarkerCluster
from folium.features import DivIcon
from streamlit_folium import st_folium
import osmnx as ox
import networkx as nx

# ===================== 설정 =====================
MAPBOX_TOKEN = os.getenv("MAPBOX_TOKEN", "YOUR_MAPBOX_TOKEN_HERE")

DATA_DIR = "."
ROUTE_FILES = {
    "DRT-1호선": os.path.join(DATA_DIR, "drt_1.shp"),
    "DRT-2호선": os.path.join(DATA_DIR, "drt_2.shp"),
    "DRT-3호선": os.path.join(DATA_DIR, "drt_3.shp"),
    "DRT-4호선": os.path.join(DATA_DIR, "drt_4.shp"),
}
MIN_GAP_M = 10.0
FALLBACK_OFFSET_M = 15.0
OSMNX_DIST_M = 5000  # 실도로 폴백용 그래프 반경

# ===================== 유틸 =====================
def haversine_m(lon1, lat1, lon2, lat2):
    R = 6371000.0
    dlon = math.radians(lon2 - lon1)
    dlat = math.radians(lat2 - lat1)
    a = math.sin(dlat/2)**2 + math.cos(math.radians(lat1))*math.cos(math.radians(lat2))*math.sin(dlon/2)**2
    return 2 * R * math.asin(math.sqrt(a))

def ensure_exists(path):
    if not os.path.exists(path):
        raise FileNotFoundError(f"파일 없음: {os.path.abspath(path)}")

# ===================== 데이터 로드 =====================
@st.cache_data
def load_drt():
    """
    - 각 drt_*.shp에서 모든 LineString/MultiLineString 좌표 수집
    - 10m 인접 중복 제거, 최소 2개 보장 → 정류장 생성
    """
    bus_routes = {}
    all_stops = []
    for route_name, shp in ROUTE_FILES.items():
        ensure_exists(shp)
        g = gpd.read_file(shp).to_crs(epsg=4326)
        bus_routes[route_name] = g
        if g.empty:
            continue

        coords_all = []
        for geom in g.geometry.dropna():
            if hasattr(geom, "coords"):
                coords_all.extend(list(geom.coords))
            elif hasattr(geom, "geoms"):
                for line in geom.geoms:
                    coords_all.extend(list(line.coords))

        filtered = []
        for (lon, lat) in coords_all:
            if not filtered:
                filtered.append((lon, lat))
            else:
                plon, plat = filtered[-1]
                if haversine_m(plon, plat, lon, lat) > MIN_GAP_M:
                    filtered.append((lon, lat))

        if len(filtered) == 1:
            lon, lat = filtered[0]
            dlat = FALLBACK_OFFSET_M / 111320.0
            filtered.append((lon, lat + dlat))

        for j, (lon, lat) in enumerate(filtered):
            all_stops.append({
                "name": f"{route_name} {j+1}번 정류장",
                "route": route_name,
                "lon": float(lon),
                "lat": float(lat),
            })

    if not all_stops:
        return None, bus_routes

    stops_df = pd.DataFrame(all_stops)
    stops_gdf = gpd.GeoDataFrame(
        stops_df, geometry=gpd.points_from_xy(stops_df.lon, stops_df.lat), crs="EPSG:4326"
    )
    # 문자열 컬럼 정규화
    stops_gdf["name"] = stops_gdf["name"].astype(str).str.strip()
    stops_gdf["route"] = stops_gdf["route"].astype(str).str.strip()
    return stops_gdf, bus_routes

try:
    stops_gdf, bus_routes = load_drt()
except Exception as e:
    st.error(f"❌ 데이터 로드 실패: {e}")
    st.stop()

if stops_gdf is None or stops_gdf.empty:
    st.error("❌ 정류장 데이터가 비어 있습니다.")
    st.stop()

# ===================== 도로 그래프(폴백) =====================
@st.cache_data
def load_graph(lat, lon, dist=OSMNX_DIST_M, net_type="drive"):
    try:
        return ox.graph_from_point((lat, lon), dist=dist, network_type=net_type)
    except Exception:
        return None

# ===================== Mapbox Directions =====================
def mapbox_route(lonlat_pairs, profile="driving"):
    segs, sec, meters = [], 0.0, 0.0
    if len(lonlat_pairs) < 2:
        return segs, sec, meters
    for i in range(len(lonlat_pairs) - 1):
        x1, y1 = lonlat_pairs[i]
        x2, y2 = lonlat_pairs[i + 1]
        url = f"https://api.mapbox.com/directions/v5/mapbox/{profile}/{x1},{y1};{x2},{y2}"
        params = {
            "geometries": "geojson",
            "overview": "full",
            "alternatives": "false",
            "steps": "false",
            "access_token": MAPBOX_TOKEN
        }
        try:
            r = requests.get(url, params=params, timeout=10)
            if r.status_code == 200 and r.json().get("routes"):
                route = r.json()["routes"][0]
                line = route["geometry"]["coordinates"]  # [[lon,lat],...]
                if line and len(line) >= 2:
                    segs.append(line)
                sec += route.get("duration", 0.0)
                meters += route.get("distance", 0.0)
            else:
                st.warning(f"Mapbox 실패(구간 {i+1}) status {r.status_code}")
        except Exception as e:
            st.warning(f"Mapbox 오류(구간 {i+1}): {e}")
    return segs, sec, meters

# ===================== OSMnx 폴백(에지 geometry 사용) =====================
def osmnx_route(G, lonlat_pairs, speed_kmh=30.0):
    if G is None or len(lonlat_pairs) < 2:
        return [], 0.0, 0.0

    # 최근접 노드 스냅
    nodes = []
    for (lon, lat) in lonlat_pairs:
        try:
            nid = ox.distance.nearest_nodes(G, lon, lat)
            nodes.append(nid)
        except Exception:
            return [], 0.0, 0.0

    segs = []
    total_m = 0.0
    for i in range(len(nodes) - 1):
        try:
            path = ox.shortest_path(G, nodes[i], nodes[i + 1], weight="length")
            if not path or len(path) < 2:
                st.warning(f"OSMnx 경로 없음(구간 {i+1})")
                continue
            geoms = ox.utils_graph.get_route_edge_attributes(G, path, "geometry")
            coords_lonlat = []
            if isinstance(geoms, list) and geoms:
                for geom in geoms:
                    if geom is None:
                        continue
                    coords_lonlat.extend(list(geom.coords))  # [(lon,lat),...]
            else:
                coords_lonlat = [[G.nodes[n]["x"], G.nodes[n]["y"]] for n in path]
            if coords_lonlat and len(coords_lonlat) >= 2:
                segs.append(coords_lonlat)

            lengths = ox.utils_graph.get_route_edge_attributes(G, path, "length")
            if isinstance(lengths, list):
                total_m += sum([l for l in lengths if l is not None])
            elif lengths is not None:
                total_m += float(lengths)
        except Exception as e:
            st.warning(f"OSMnx 경로 실패(구간 {i+1}): {e}")

    mps = speed_kmh * 1000 / 3600.0
    total_sec = total_m / mps if mps > 0 else 0.0
    return segs, total_sec, total_m

# ===================== 페이지 & 스타일 =====================
st.set_page_config(page_title="천안 DRT - 실도로 네비게이션 경로", layout="wide", initial_sidebar_state="collapsed")
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Noto+Sans+KR:wght@300;400;500;600;700&display=swap');
html, body, [class*="css"] { font-family:'Noto Sans KR', -apple-system, BlinkMacSystemFont, sans-serif; }
header[data-testid="stHeader"] { display:none; }
.stApp { background:#f8f9fa; }
.section-title { font-size:1.15rem; font-weight:700; color:#1f2937; margin:.6rem 0 .4rem 0; }
.map-container { width:100%!important; height:560px!important; border-radius:12px!important; border:2px solid #e5e7eb!important; overflow:hidden!important; }
div[data-testid="stIFrame"], div[data-testid="stIFrame"] > iframe,
.folium-map, .leaflet-container { width:100%!important; height:560px!important; border:none!important; border-radius:12px!important; background:transparent!important; }
.visit { display:flex; align-items:center; gap:8px; background:#667eea; color:#fff; padding:8px 12px; border-radius:10px; margin-bottom:6px; }
.badge { background:#fff; color:#667eea; width:22px; height:22px; border-radius:50%; display:flex; align-items:center; justify-content:center; font-weight:700; font-size:.8rem; }
</style>
""", unsafe_allow_html=True)

st.title("🚌 천안 DRT 실도로 기반 최적 경로")

col1, col2, col3 = st.columns([1.4, 1.1, 3], gap="large")

# ===================== 좌: 입력 =====================
with col1:
    st.markdown('<div class="section-title">운행 설정</div>', unsafe_allow_html=True)
    route_names = list(bus_routes.keys())
    selected_route = st.selectbox("노선 선택", route_names)

    r_stops = stops_gdf.loc[stops_gdf["route"] == selected_route, "name"].astype(str).tolist()
    start = st.selectbox("출발 정류장", r_stops)
    ends = [s for s in r_stops if s != start] or r_stops
    end = st.selectbox("도착 정류장", ends)

    mode = st.radio("이동 모드", ["운전자(도로)", "도보(보행로)"], horizontal=True)
    profile = "driving" if "운전자" in mode else "walking"

    st.caption("Mapbox Directions로 실도로 경로 생성 → 실패 시 OSMnx(에지 geometry) 폴백.")
    generate = st.button("노선 최적화")
    clear = st.button("초기화", type="secondary")

if clear:
    st.session_state["segments"] = []
    st.session_state["order"] = []
    st.session_state["duration"] = 0.0
    st.session_state["distance"] = 0.0
    st.success("✅ 초기화 완료")

# ===================== 인덱싱 안전 함수 =====================
def name_to_lonlat(stop_name):
    # 단일 문자열로 강제
    if isinstance(stop_name, (list, tuple, set)):
        if not stop_name:
            return None
        stop_name = list(stop_name)[0]
    stop_name = str(stop_name)

    r = stops_gdf.loc[stops_gdf["name"].astype(str) == stop_name]
    if r.empty:
        st.warning(f"좌표 조회 실패: '{stop_name}'을(를) 찾을 수 없습니다.")
        return None
    try:
        row = r.iloc
    except Exception as e:
        st.warning(f"행 인덱싱 오류: {e} (stop_name={stop_name}, index={list(r.index)[:5]})")
        return None
    lon = float(row["lon"])
    lat = float(row["lat"])
    if math.isnan(lon) or math.isnan(lat):
        st.warning(f"좌표 NaN: '{stop_name}'")
        return None
    return lon, lat

# 세션 기본값
for k, v in {"segments": [], "order": [], "duration": 0.0, "distance": 0.0}.items():
    if k not in st.session_state:
        st.session_state[k] = v

# ===================== 경로 생성 =====================
if generate:
    try:
        if not isinstance(start, str) or not isinstance(end, str):
            st.error("출발/도착 정류장 선택이 올바르지 않습니다. 다시 선택해 주세요.")
        else:
            s = name_to_lonlat(start)
            e = name_to_lonlat(end)
            coords = [c for c in [s, e] if c is not None]
            if len(coords) < 2:
                st.error("경로 생성에 필요한 좌표가 부족합니다. 출발/도착을 다시 선택하세요.")
            else:
                # 1) Mapbox Directions
                segs, sec, meters = mapbox_route(coords, profile=profile)

                # 2) OSMnx 폴백
                if not segs:
                    avg_lat = sum([c[1] for c in coords]) / len(coords)
                    avg_lon = sum([c for c in coords]) / len(coords)
                    net_type = "drive" if profile == "driving" else "walk"
                    G = load_graph(avg_lat, avg_lon, dist=OSMNX_DIST_M, net_type=net_type)
                    spd = 30.0 if profile == "driving" else 4.5
                    segs, sec, meters = osmnx_route(G, coords, speed_kmh=spd)

                if segs:
                    st.session_state["segments"] = segs
                    st.session_state["order"] = [start, end]
                    st.session_state["duration"] = sec / 60.0
                    st.session_state["distance"] = meters / 1000.0
                    st.success("✅ 실도로 기반 노선 최적화 완료")
                else:
                    st.error("❌ 경로 생성 실패: 정류장 조합/범위·토큰을 확인해 주세요.")

    except Exception as e:
        st.error(f"❌ 경로 생성 오류: {e}")
        st.write("디버그:", {"start": start, "end": end, "type(start)": type(start), "type(end)": type(end)})

# ===================== 중: 요약 =====================
with col2:
    st.markdown('<div class="section-title">운행 순서</div>', unsafe_allow_html=True)
    if st.session_state.get("order"):
        for i, nm in enumerate(st.session_state["order"], 1):
            st.markdown(f'<div class="visit"><div class="badge">{i}</div><div>{nm}</div></div>', unsafe_allow_html=True)
    else:
        st.info("경로를 생성하면 순서가 표시됩니다.")
    st.metric("⏱️ 예상 소요시간", f"{st.session_state.get('duration', 0.0):.1f}분")
    st.metric("📏 예상 이동거리", f"{st.session_state.get('distance', 0.0):.2f}km")

# ===================== 우: 지도 =====================
with col3:
    clat, clon = float(stops_gdf["lat"].mean()), float(stops_gdf["lon"].mean())
    m = folium.Map(location=[clat, clon], zoom_start=13, tiles="CartoDB Positron",
                   prefer_canvas=True, control_scale=True)

    # 원본 노선(얇게)
    colors = {"DRT-1호선":"#4285f4","DRT-2호선":"#ea4335","DRT-3호선":"#34a853","DRT-4호선":"#fbbc04"}
    g = bus_routes.get(selected_route)
    if g is not None and not g.empty:
        coords = []
        for geom in g.geometry.dropna():
            if hasattr(geom, "coords"):
                coords.extend([(y, x) for x, y in geom.coords])
            elif hasattr(geom, "geoms"):
                for line in geom.geoms:
                    coords.extend([(y, x) for x, y in line.coords])
        if coords:
            folium.PolyLine(coords, color=colors.get(selected_route, "#666"),
                            weight=3, opacity=0.35, tooltip=f"{selected_route} (원본)").add_to(m)

    # 정류장(선택 노선)
    mc = MarkerCluster().add_to(m)
    for _, row in stops_gdf[stops_gdf["route"] == selected_route].iterrows():
        folium.Marker([row["lat"], row["lon"]],
                      popup=folium.Popup(f"<b>{row['name']}</b>", max_width=220),
                      tooltip=row["name"],
                      icon=folium.Icon(color="blue", icon="bus", prefix="fa")).add_to(mc)

    # 실도로 경로
    segs = st.session_state.get("segments", [])
    if segs:
        palette = ["#3f7cff", "#00b894", "#ff7675", "#fdcb6e", "#6c5ce7"]
        for i, seg in enumerate(segs):
            latlon = [(p[1], p) for p in seg]  # [[lon,lat]] -> [(lat,lon)]
            folium.PolyLine(latlon, color=palette[i % len(palette)],
                            weight=7, opacity=0.92, tooltip=f"실도로 경로 {i+1}").add_to(m)

        # 출발/도착 강조
        if st.session_state.get("order"):
            s_nm, e_nm = st.session_state["order"][0], st.session_state["order"][-1]
            s_row = stops_gdf[stops_gdf["name"] == s_nm].iloc
            e_row = stops_gdf[stops_gdf["name"] == e_nm].iloc
            folium.Marker([s_row["lat"], s_row["lon"]],
                          icon=folium.Icon(color="green", icon="play", prefix="fa"),
                          tooltip=f"출발: {s_nm}").add_to(m)
            folium.Marker([e_row["lat"], e_row["lon"]],
                          icon=folium.Icon(color="red", icon="stop", prefix="fa"),
                          tooltip=f"도착: {e_nm}").add_to(m)

    st.markdown('<div class="map-container">', unsafe_allow_html=True)
    st_folium(m, width="100%", height=560, returned_objects=[], use_container_width=True, key="drt_nav_map")
    st.markdown('</div>', unsafe_allow_html=True)
