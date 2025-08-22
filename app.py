import os
import math
import time
import requests
import pandas as pd
import geopandas as gpd
import streamlit as st
import folium
from shapely.geometry import Point
from folium.plugins import MarkerCluster
from folium.features import DivIcon
from streamlit_folium import st_folium

# =============== 설정 ===============
MAPBOX_TOKEN = os.getenv("MAPBOX_TOKEN", "YOUR_MAPBOX_TOKEN_HERE")

DATA_DIR = "."  # drt_*.shp 파일이 위치한 폴더
ROUTE_FILES = {
    "DRT-1호선": os.path.join(DATA_DIR, "drt_1.shp"),
    "DRT-2호선": os.path.join(DATA_DIR, "drt_2.shp"),
    "DRT-3호선": os.path.join(DATA_DIR, "drt_3.shp"),
    "DRT-4호선": os.path.join(DATA_DIR, "drt_4.shp"),
}
MIN_GAP_M = 10.0           # 정류장 최소 간격(중복 제거 기준)
FALLBACK_OFFSET_M = 15.0   # 좌표 1개일 때 보조점 추가 거리

# =============== 유틸 ===============
def haversine_m(lon1, lat1, lon2, lat2):
    R = 6371000.0
    dlon = math.radians(lon2 - lon1)
    dlat = math.radians(lat2 - lat1)
    a = math.sin(dlat/2)**2 + math.cos(math.radians(lat1))*math.cos(math.radians(lat2))*math.sin(dlon/2)**2
    return 2*R*math.asin(math.sqrt(a)) * R / R  # 가독용 동일표기

def ensure_exists(path):
    if not os.path.exists(path):
        raise FileNotFoundError(f"파일이 없습니다: {os.path.abspath(path)}")

# =============== 데이터 로드 ===============
@st.cache_data
def load_drt():
    """
    - drt_1~4 라인셋에서 모든 좌표 수집
    - 인접 중복 제거(10m)
    - 최소 2개 정류장 보장(1개면 북쪽으로 15m 보조점 추가)
    반환:
      - stops_gdf: 정류장 포인트 GeoDataFrame [name, route, lon, lat]
      - routes: {노선명: LineString/MultiLineString GeoDataFrame}
    """
    bus_routes = {}
    all_stops = []

    for route_name, shp in ROUTE_FILES.items():
        ensure_exists(shp)
        route_gdf = gpd.read_file(shp).to_crs(epsg=4326)
        bus_routes[route_name] = route_gdf

        if route_gdf is None or route_gdf.empty:
            continue

        # 모든 지오메트리 좌표 수집
        coords_all = []
        for geom in route_gdf.geometry.dropna():
            if hasattr(geom, "coords"):           # LineString
                coords_all.extend(list(geom.coords))
            elif hasattr(geom, "geoms"):          # MultiLineString
                for line in geom.geoms:
                    coords_all.extend(list(line.coords))

        # 인접 중복 제거
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

        # 정류장 생성
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
except FileNotFoundError as e:
    st.error(f"❌ 데이터 로드 실패: {e}")
    st.stop()
except Exception as e:
    st.error(f"❌ 데이터 로드 실패: {e}")
    st.stop()

if stops_gdf is None or stops_gdf.empty:
    st.error("❌ 정류장 데이터가 비어있습니다. drt_*.shp를 확인하세요.")
    st.stop()

# =============== 페이지/스타일 ===============
st.set_page_config(page_title="천안 DRT 기본 베이스", layout="wide", initial_sidebar_state="collapsed")
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Noto+Sans+KR:wght@300;400;500;600;700&display=swap');
html, body, [class*="css"] { font-family:'Noto Sans KR', -apple-system, BlinkMacSystemFont, sans-serif; }
header[data-testid="stHeader"] { display:none; }
.stApp { background:#f8f9fa; }
.section { margin-top:10px; margin-bottom:12px; }
.section-title { font-size:1.1rem; font-weight:700; color:#1f2937; margin-bottom:8px; }
.map-container { width:100%!important; height:520px!important; border-radius:12px!important; border:2px solid #e5e7eb!important; overflow:hidden!important; }
div[data-testid="stIFrame"], div[data-testid="stIFrame"] > iframe,
.folium-map, .leaflet-container { width:100%!important; height:520px!important; border:none!important; border-radius:12px!important; background:transparent!important; }
.visit { display:flex; align-items:center; gap:8px; background:#667eea; color:#fff; padding:8px 12px; border-radius:10px; margin-bottom:6px; }
.badge { background:#fff; color:#667eea; width:22px; height:22px; border-radius:50%; display:flex; align-items:center; justify-content:center; font-weight:700; font-size:.8rem; }
</style>
""", unsafe_allow_html=True)

st.title("🚌 천안 DRT 베이스")

col1, col2, col3 = st.columns([1.3, 1.2, 3], gap="large")

# =============== 좌: 입력 ===============
with col1:
    st.markdown('<div class="section-title">운행 설정</div>', unsafe_allow_html=True)

    route_names = list(bus_routes.keys())
    selected_route = st.selectbox("노선 선택", route_names)

    # 해당 노선 정류장
    route_stops = stops_gdf.loc[stops_gdf["route"] == selected_route, "name"].tolist()

    start = st.selectbox("출발 정류장", route_stops)
    ends = [s for s in route_stops if s != start] or route_stops
    end = st.selectbox("도착 정류장", ends)

    mode = st.radio("이동 모드", ["운전자", "도보"], horizontal=True)
    profile = "driving" if mode == "운전자" else "walking"

    generate = st.button("노선 최적화")

# =============== 경로 생성 ===============
def name_to_lonlat(stop_name):
    r = stops_gdf[stops_gdf["name"] == stop_name]
    if r.empty:
        return None
    return float(r.iloc[0]["lon"]), float(r.iloc["lat"])

def mapbox_route(lonlat_pairs, profile="driving"):
    segs, sec, meters = [], 0.0, 0.0
    for i in range(len(lonlat_pairs) - 1):
        x1, y1 = lonlat_pairs[i]
        x2, y2 = lonlat_pairs[i + 1]
        url = f"https://api.mapbox.com/directions/v5/mapbox/{profile}/{x1},{y1};{x2},{y2}"
        params = {"geometries": "geojson", "overview": "full", "access_token": MAPBOX_TOKEN}
        r = requests.get(url, params=params, timeout=10)
        if r.status_code == 200 and r.json().get("routes"):
            rt = r.json()["routes"][0]
            segs.append(rt["geometry"]["coordinates"])
            sec += rt.get("duration", 0.0)
            meters += rt.get("distance", 0.0)
        else:
            st.warning(f"경로 호출 실패(구간 {i+1}) - status {r.status_code}")
    return segs, sec, meters

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

        segs, sec, m = mapbox_route(coords, profile)
        if segs:
            st.session_state["segments"] = segs
            st.session_state["order"] = [start, end]
            st.session_state["duration"] = sec / 60.0
            st.session_state["distance"] = m / 1000.0
            st.success("✅ 노선 최적화가 완료되었습니다!")
        else:
            st.error("❌ 경로를 생성하지 못했습니다. 정류장을 바꿔 시도해 보세요.")
    except Exception as e:
        st.error(f"❌ 경로 생성 오류: {e}")

# =============== 중: 요약 ===============
with col2:
    st.markdown('<div class="section-title">운행 순서</div>', unsafe_allow_html=True)
    if st.session_state.get("order"):
        for i, nm in enumerate(st.session_state["order"], 1):
            st.markdown(f'<div class="visit"><div class="badge">{i}</div><div>{nm}</div></div>', unsafe_allow_html=True)
    else:
        st.info("경로를 생성하면 순서가 표시됩니다.")

    st.metric("⏱️ 예상 소요시간", f"{st.session_state.get('duration', 0.0):.1f}분")
    st.metric("📏 예상 이동거리", f"{st.session_state.get('distance', 0.0):.2f}km")

# =============== 우: 지도 ===============
with col3:
    st.markdown('<div class="section-title">지도</div>', unsafe_allow_html=True)
    try:
        clat, clon = float(stops_gdf["lat"].mean()), float(stops_gdf["lon"].mean())
    except Exception:
        clat, clon = 36.8151, 127.1139

    m = folium.Map(location=[clat, clon], zoom_start=13, tiles="CartoDB Positron",
                   prefer_canvas=True, control_scale=True)

    # 노선 라인(선택 노선만 표시)
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
                            weight=5, opacity=0.8, tooltip=f"{selected_route}").add_to(m)

    # 정류장 마커(선택 노선만)
    mc = MarkerCluster().add_to(m)
    for _, row in stops_gdf[stops_gdf["route"] == selected_route].iterrows():
        folium.Marker([row["lat"], row["lon"]],
                      popup=folium.Popup(f"<b>{row['name']}</b>", max_width=220),
                      tooltip=row["name"],
                      icon=folium.Icon(color="blue", icon="bus", prefix="fa")).add_to(mc)

    # 생성된 경로
    segs = st.session_state.get("segments", [])
    if segs:
        palette = ["#4285f4", "#34a853", "#ea4335", "#fbbc04"]
        for i, seg in enumerate(segs):
            latlon = [(p[1], p) for p in seg]
            folium.PolyLine(latlon, color=palette[i % len(palette)],
                            weight=6, opacity=0.9, tooltip=f"경로 {i+1}").add_to(m)
        mid = segs[len(segs)//2]
        folium.map.Marker([mid[1], mid],
                          icon=DivIcon(html="<div style='background:#4285f4;color:#fff;border-radius:50%;"
                                            "width:28px;height:28px;line-height:28px;text-align:center;"
                                            "font-weight:700;'>1</div>")
                          ).add_to(m)

    st.markdown('<div class="map-container">', unsafe_allow_html=True)
    st_folium(m, width="100%", height=520, returned_objects=[], use_container_width=True, key="drt_base_map")
    st.markdown('</div>', unsafe_allow_html=True)
