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

DATA_DIR = "."  # drt_*.shp 파일 폴더
ROUTE_FILES = {
    "DRT-1호선": os.path.join(DATA_DIR, "drt_1.shp"),
    "DRT-2호선": os.path.join(DATA_DIR, "drt_2.shp"),
    "DRT-3호선": os.path.join(DATA_DIR, "drt_3.shp"),
    "DRT-4호선": os.path.join(DATA_DIR, "drt_4.shp"),
}
MIN_GAP_M = 10.0
FALLBACK_OFFSET_M = 15.0
OSMNX_DIST_M = 5000  # 도로 그래프 범위(반경)

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
            if hasattr(geom, "coords"):         # LineString
                coords_all.extend(list(geom.coords))
            elif hasattr(geom, "geoms"):        # MultiLineString
                for line in geom.geoms:
                    coords_all.extend(list(line.coords))

        # 인접 중복 제거(10m)
        filtered = []
        for (lon, lat) in coords_all:
            if not filtered:
                filtered.append((lon, lat))
            else:
                plon, plat = filtered[-1]
                if haversine_m(plon, plat, lon, lat) > MIN_GAP_M:
                    filtered.append((lon, lat))

        # 최소 2개 보장
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
    return stops_gdf, bus_routes

stops_gdf, bus_routes = None, None
try:
    stops_gdf, bus_routes = load_drt()
except Exception as e:
    st.error(f"❌ 데이터 로드 실패: {e}")
    st.stop()

if stops_gdf is None or stops_gdf.empty:
    st.error("❌ 정류장 데이터가 비어 있습니다.")
    st.stop()

# ===================== 도로 그래프 =====================
@st.cache_data
def load_graph(lat, lon, dist=OSMNX_DIST_M):
    try:
        return ox.graph_from_point((lat, lon), dist=dist, network_type="drive")  # 운전 기준 네트워크
    except Exception:
        return None

# ===================== Mapbox Directions =====================
def mapbox_route(lonlat_pairs, profile="driving"):
    """
    - lonlat_pairs: [(lon, lat), (lon, lat), ...]
    - profile: "driving" 또는 "walking"
    반환: (segments[[[lon,lat],...],...], total_sec, total_meters)
    """
    segs, sec, meters = [], 0.0, 0.0
    if len(lonlat_pairs) < 2:
        return segs, sec, meters
    for i in range(len(lonlat_pairs) - 1):
        x1, y1 = lonlat_pairs[i]
        x2, y2 = lonlat_pairs[i + 1]
        url = f"https://api.mapbox.com/directions/v5/mapbox/{profile}/{x1},{y1};{x2},{y2}"
        params = {
            "geometries": "geojson",
            "overview": "full",          # 고해상도 polyline
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
    """
    - 최근접 노드로 스냅 → shortest_path
    - 각 에지의 geometry(LineString)를 이어붙여 polyline 생성
    """
    if G is None or len(lonlat_pairs) < 2:
        return [], 0.0, 0.0

    # 최근접 노드 스냅
    nodes = []
    for (lon, lat) in lonlat_pairs:
        try:
            nid = ox.distance.nearest_nodes(G, lon, lat)  # (x=lon, y=lat)
            nodes.append(nid)
        except Exception:
            return [], 0.0, 0.0

    segs = []
    total_m = 0.0
    for i in range(len(nodes) - 1):
        try:
            path = ox.shortest_path(G, nodes[i], nodes[i + 1], weight="length")
            # 에지 geometry 수집
            geoms = ox.utils_graph.get_route_edge_attributes(G, path, "geometry")
            coords_lonlat = []
            if isinstance(geoms, list) and geoms:
                for geom in geoms:
                    if geom is None:
                        continue
                    coords_lonlat.extend(list(geom.coords))  # [(lon,lat),...]
            else:
                # geometry가 없으면 노드 좌표로 보강(직선화 가능성)
                coords_lonlat = [[G.nodes[n]["x"], G.nodes[n]["y"]] for n in path]
            if coords_lonlat and len(coords_lonlat) >= 2:
                segs.append(coords_lonlat)
            # 거리 합산
            lengths = ox.utils_graph.get_route_edge_attributes(G, path, "length")
            if isinstance(lengths, list):
                total_m += sum([l for l in lengths if l is not None])
            elif lengths is not None:
                total_m += float(lengths)
        except Exception as e:
            st.warning(f"OSMnx 경로 실패(구간 {i+1}): {e}")

    # 시간 추정
    mps = speed_kmh * 1000 / 3600.0
    total_sec = total_m / mps if mps > 0 else 0.0
    return segs, total_sec, total_m

# ===================== 페이지/스타일 =====================
st.set_page_config(page_title="천안 DRT 실도로 네비게이션", layout="wide", initial_sidebar_state="collapsed")
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Noto+Sans+KR:wght@300;400;500;600;700&display=swap');
html, body, [class*="css"] { font-family:'Noto Sans KR', -apple-system, BlinkMacSystemFont, sans-serif; }
header[data-testid="stHeader"] { display:none; }
.stApp { background:#f8f9fa; }
.section-title { font-size:1.2rem; font-weight:700; color:#1f2937; margin:.6rem 0 .4rem 0; }
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

    r_stops = stops_gdf.loc[stops_gdf["route"] == selected_route, "name"].tolist()
    start = st.selectbox("출발 정류장", r_stops)
    ends = [s for s in r_stops if s != start] or r_stops
    end = st.selectbox("도착 정류장", ends)

    mode = st.radio("이동 모드", ["운전자(도로)", "도보(보행로)"], horizontal=True)
    profile = "driving" if "운전자" in mode else "walking"

    st.caption("우선 Mapbox Directions로 실도로 경로를 계산하고, 실패 시 OSMnx 최단경로(에지 geometry)로 폴백합니다.")
    generate = st.button("노선 최적화")

# ===================== 경로 생성 =====================
def name_to_lonlat(stop_name):
    r = stops_gdf[stops_gdf["name"] == stop_name]
    if r.empty:
        return None
    return float(r.iloc[0]["lon"]), float(r.iloc["lat"])

if "segments" not in st.session_state:
    st.session_state["segments"] = []
    st.session_state["order"] = []
    st.session_state["duration"] = 0.0
    st.session_state["distance"] = 0.0

if generate:
    try:
        s = name_to_lonlat(start)
        e = name_to_lonlat(end)
        coords = []
        if s: coords.append(s)
        if e: coords.append(e)
        if len(coords) == 1:
            x, y = coords[0]
            coords.append((x + 0.0005, y))  # 보조 목적지

        # 1) Mapbox 실도로 경로(고해상도 polyline)
        segs, sec, meters = mapbox_route(coords, profile=profile)

        # 2) 폴백: OSMnx 도로 그래프 최단경로(에지 geometry 이어붙이기)
        if not segs:
            avg_lat = sum([c[1] for c in coords]) / len(coords)
            avg_lon = sum([c for c in coords]) / len(coords)
            G = load_graph(avg_lat, avg_lon, dist=OSMNX_DIST_M)
            spd = 30.0 if profile == "driving" else 4.5
            segs, sec, meters = osmnx_route(G, coords, speed_kmh=spd)

        if segs:
            st.session_state["segments"] = segs
            st.session_state["order"] = [start, end]
            st.session_state["duration"] = sec / 60.0
            st.session_state["distance"] = meters / 1000.0
            st.success("✅ 실도로 기반 노선 최적화 완료")
        else:
            st.error("❌ 경로를 생성하지 못했습니다. 정류장 조합/범위를 조정해 보세요.")
    except Exception as e:
        st.error(f"❌ 경로 생성 오류: {e}")

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
    try:
        clat, clon = float(stops_gdf["lat"].mean()), float(stops_gdf["lon"].mean())
    except Exception:
        clat, clon = 36.8151, 127.1139

    m = folium.Map(location=[clat, clon], zoom_start=13, tiles="CartoDB Positron",
                   prefer_canvas=True, control_scale=True)

    # 선택 노선 원본 라인(참고용 얇게)
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
                            weight=3, opacity=0.4, tooltip=f"{selected_route} (원본)").add_to(m)

    # 정류장(선택 노선만)
    mc = MarkerCluster().add_to(m)
    for _, row in stops_gdf[stops_gdf["route"] == selected_route].iterrows():
        folium.Marker([row["lat"], row["lon"]],
                      popup=folium.Popup(f"<b>{row['name']}</b>", max_width=220),
                      tooltip=row["name"],
                      icon=folium.Icon(color="blue", icon="bus", prefix="fa")).add_to(mc)

    # 실도로 경로(세그먼트 → 라인: Mapbox 또는 OSMnx 결과)
    segs = st.session_state.get("segments", [])
    if segs:
        palette = ["#ff5722", "#009688", "#3f51b5", "#9c27b0", "#795548"]
        for i, seg in enumerate(segs):
            # seg: [[lon,lat], ...]
            latlon = [(p[1], p) for p in seg]
            folium.PolyLine(latlon, color=palette[i % len(palette)],
                            weight=7, opacity=0.9, tooltip=f"실도로 경로 {i+1}").add_to(m)

        # 시작/끝 강조
        if st.session_state.get("order"):
            s_nm, e_nm = st.session_state["order"], st.session_state["order"][-1]
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
