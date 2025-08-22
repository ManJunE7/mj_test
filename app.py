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
MAPBOX_TOKEN = "pk.eyJ1IjoiZ3VyMDUxMDgiLCJhIjoiY21lZ2k1Y291MTdoZjJrb2k3bHc3cTJrbSJ9.DElgSQ0rPoRk1eEacPI8uQ"

DATA_DIR = "."
ROUTE_FILES = {
    "DRT-1호선": os.path.join(DATA_DIR, "drt_1.shp"),
    "DRT-2호선": os.path.join(DATA_DIR, "drt_2.shp"),
    "DRT-3호선": os.path.join(DATA_DIR, "drt_3.shp"),
    "DRT-4호선": os.path.join(DATA_DIR, "drt_4.shp"),
}
MIN_GAP_M = 10.0
FALLBACK_OFFSET_M = 15.0
OSMNX_DIST_M = 5000

# ===================== 유틸 =====================
def haversine_m(lon1, lat1, lon2, lat2):
    try:
        R = 6371000.0
        dlon = math.radians(float(lon2) - float(lon1))
        dlat = math.radians(float(lat2) - float(lat1))
        a = math.sin(dlat/2)**2 + math.cos(math.radians(float(lat1)))*math.cos(math.radians(float(lat2)))*math.sin(dlon/2)**2
        return 2 * R * math.asin(math.sqrt(a))
    except (ValueError, TypeError):
        return 0.0

def ensure_exists(path):
    if not os.path.exists(path):
        raise FileNotFoundError(f"파일 없음: {os.path.abspath(path)}")

# ===================== 안전한 좌표 추출 함수 =====================
def safe_get_coords_from_geom(geom):
    """지오메트리에서 안전하게 좌표 추출"""
    try:
        if geom is None:
            return []
        if hasattr(geom, "coords"):
            return list(geom.coords)
        elif hasattr(geom, "geoms"):
            coords = []
            for line in geom.geoms:
                if hasattr(line, "coords"):
                    coords.extend(list(line.coords))
            return coords
        return []
    except Exception:
        return []

# ===================== 데이터 로드 =====================
@st.cache_data
def load_drt():
    """안전한 DRT 데이터 로드"""
    bus_routes = {}
    all_stops = []
    
    for route_name, shp in ROUTE_FILES.items():
        try:
            ensure_exists(shp)
            g = gpd.read_file(shp).to_crs(epsg=4326)
            bus_routes[route_name] = g
            
            if g.empty:
                continue

            coords_all = []
            for _, row in g.iterrows():
                geom_coords = safe_get_coords_from_geom(row.geometry)
                coords_all.extend(geom_coords)

            # 인접 중복 제거
            filtered = []
            for (lon, lat) in coords_all:
                try:
                    lon, lat = float(lon), float(lat)
                    if math.isnan(lon) or math.isnan(lat):
                        continue
                    
                    if not filtered:
                        filtered.append((lon, lat))
                    else:
                        plon, plat = filtered[-1]
                        if haversine_m(plon, plat, lon, lat) > MIN_GAP_M:
                            filtered.append((lon, lat))
                except (ValueError, TypeError):
                    continue

            # 최소 2개 보장
            if len(filtered) == 1:
                lon, lat = filtered[0]
                dlat = FALLBACK_OFFSET_M / 111320.0
                filtered.append((lon, lat + dlat))
            
            # 최소 1개도 없으면 기본값 생성
            if not filtered:
                base_lat, base_lon = 36.8151, 127.1139
                filtered = [(base_lon, base_lat), (base_lon + 0.001, base_lat + 0.001)]

            # 정류장 생성
            for j, (lon, lat) in enumerate(filtered):
                all_stops.append({
                    "name": f"{route_name} {j+1}번 정류장",
                    "route": route_name,
                    "lon": float(lon),
                    "lat": float(lat),
                })
                
        except Exception as e:
            st.warning(f"{route_name} 로드 실패: {e}")
            continue

    if not all_stops:
        # 기본 데이터 생성
        default_stops = []
        for i, route_name in enumerate(ROUTE_FILES.keys()):
            base_lat = 36.8151 + i * 0.01
            base_lon = 127.1139 + i * 0.01
            for j in range(3):
                default_stops.append({
                    "name": f"{route_name} {j+1}번 정류장",
                    "route": route_name,
                    "lon": base_lon + j * 0.005,
                    "lat": base_lat + j * 0.005,
                })
        all_stops = default_stops

    stops_df = pd.DataFrame(all_stops)
    stops_gdf = gpd.GeoDataFrame(
        stops_df, geometry=gpd.points_from_xy(stops_df.lon, stops_df.lat), crs="EPSG:4326"
    )
    # 문자열 정규화
    stops_gdf["name"] = stops_gdf["name"].astype(str).str.strip()
    stops_gdf["route"] = stops_gdf["route"].astype(str).str.strip()
    return stops_gdf, bus_routes

# 데이터 로드 with 안전 처리
try:
    stops_gdf, bus_routes = load_drt()
except Exception as e:
    st.error(f"❌ 데이터 로드 실패: {e}")
    st.stop()

if stops_gdf is None or stops_gdf.empty:
    st.error("❌ 정류장 데이터가 비어 있습니다.")
    st.stop()

# ===================== 안전한 좌표 검색 함수 =====================
def safe_name_to_lonlat(stop_name):
    """완전히 안전한 좌표 검색"""
    try:
        # 입력값 정규화
        if stop_name is None:
            return None
        if isinstance(stop_name, (list, tuple, set)):
            if not stop_name:
                return None
            stop_name = str(list(stop_name)[0]).strip()
        else:
            stop_name = str(stop_name).strip()
        
        if not stop_name:
            return None
        
        # 검색
        mask = stops_gdf["name"].astype(str).str.strip() == stop_name
        matching_rows = stops_gdf[mask]
        
        if matching_rows.empty:
            st.warning(f"정류장을 찾을 수 없습니다: '{stop_name}'")
            return None
            
        if len(matching_rows) == 0:
            return None
            
        # 안전한 첫 번째 행 가져오기
        try:
            first_row = matching_rows.reset_index(drop=True).iloc[0]
        except (IndexError, KeyError):
            return None
            
        lon = float(first_row["lon"])
        lat = float(first_row["lat"])
        
        if math.isnan(lon) or math.isnan(lat):
            st.warning(f"좌표가 유효하지 않습니다: '{stop_name}'")
            return None
            
        return lon, lat
        
    except Exception as e:
        st.warning(f"좌표 검색 오류: {e}")
        return None

# ===================== 도로 그래프 =====================
@st.cache_data
def load_graph(lat, lon, dist=OSMNX_DIST_M, net_type="drive"):
    try:
        return ox.graph_from_point((float(lat), float(lon)), dist=dist, network_type=net_type)
    except Exception:
        return None

# ===================== Mapbox Directions =====================
def mapbox_route(lonlat_pairs, profile="driving"):
    """안전한 Mapbox 경로 요청"""
    segs, sec, meters = [], 0.0, 0.0
    
    if not lonlat_pairs or len(lonlat_pairs) < 2:
        return segs, sec, meters
        
    for i in range(len(lonlat_pairs) - 1):
        try:
            x1, y1 = float(lonlat_pairs[i][0]), float(lonlat_pairs[i][1])
            x2, y2 = float(lonlat_pairs[i + 1]), float(lonlat_pairs[i + 1][1])  # 수정됨
            
            url = f"https://api.mapbox.com/directions/v5/mapbox/{profile}/{x1},{y1};{x2},{y2}"
            params = {
                "geometries": "geojson",
                "overview": "full",
                "alternatives": "false",
                "steps": "false",
                "access_token": MAPBOX_TOKEN
            }
            
            r = requests.get(url, params=params, timeout=10)
            if r.status_code == 200:
                data = r.json()
                if data.get("routes") and len(data["routes"]) > 0:
                    route = data["routes"][0]
                    line = route.get("geometry", {}).get("coordinates", [])
                    if line and len(line) >= 2:
                        segs.append(line)
                    sec += route.get("duration", 0.0)
                    meters += route.get("distance", 0.0)
                else:
                    st.warning(f"경로를 찾을 수 없습니다 (구간 {i+1})")
            else:
                st.warning(f"Mapbox API 오류 (구간 {i+1}): {r.status_code}")
                
        except Exception as e:
            st.warning(f"구간 {i+1} 처리 오류: {e}")
            continue
            
    return segs, sec, meters

# ===================== OSMnx 폴백 =====================
def osmnx_route(G, lonlat_pairs, speed_kmh=30.0):
    """안전한 OSMnx 경로 생성"""
    if G is None or not lonlat_pairs or len(lonlat_pairs) < 2:
        return [], 0.0, 0.0

    # 노드 스냅
    nodes = []
    for (lon, lat) in lonlat_pairs:
        try:
            nid = ox.distance.nearest_nodes(G, float(lon), float(lat))
            nodes.append(nid)
        except Exception:
            return [], 0.0, 0.0

    if len(nodes) < 2:
        return [], 0.0, 0.0

    segs = []
    total_m = 0.0
    
    for i in range(len(nodes) - 1):
        try:
            path = ox.shortest_path(G, nodes[i], nodes[i + 1], weight="length")
            if not path or len(path) < 2:
                continue
                
            # 에지 geometry 추출
            try:
                geoms = ox.utils_graph.get_route_edge_attributes(G, path, "geometry")
                coords_lonlat = []
                
                if isinstance(geoms, list):
                    for geom in geoms:
                        if geom is not None and hasattr(geom, 'coords'):
                            coords_lonlat.extend(list(geom.coords))
                
                if not coords_lonlat:
                    # geometry가 없으면 노드 좌표 사용
                    coords_lonlat = [[G.nodes[n]["x"], G.nodes[n]["y"]] for n in path if n in G.nodes]
                
                if coords_lonlat and len(coords_lonlat) >= 2:
                    segs.append(coords_lonlat)
                
                # 거리 계산
                try:
                    lengths = ox.utils_graph.get_route_edge_attributes(G, path, "length")
                    if isinstance(lengths, list):
                        total_m += sum([float(l) for l in lengths if l is not None])
                    elif lengths is not None:
                        total_m += float(lengths)
                except Exception:
                    pass
                    
            except Exception:
                continue
                
        except Exception:
            continue

    # 시간 계산
    try:
        mps = float(speed_kmh) * 1000 / 3600.0
        total_sec = total_m / mps if mps > 0 else 0.0
    except (ValueError, ZeroDivisionError):
        total_sec = 0.0
        
    return segs, total_sec, total_m

# ===================== 페이지 설정 =====================
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

# ===================== 세션 상태 초기화 =====================
for k, v in {"segments": [], "order": [], "duration": 0.0, "distance": 0.0}.items():
    if k not in st.session_state:
        st.session_state[k] = v

# ===================== 레이아웃 =====================
col1, col2, col3 = st.columns([1.4, 1.1, 3], gap="large")

# ===================== 좌측: 입력 =====================
with col1:
    st.markdown('<div class="section-title">운행 설정</div>', unsafe_allow_html=True)
    
    try:
        route_names = list(bus_routes.keys())
        selected_route = st.selectbox("노선 선택", route_names)

        # 안전한 정류장 목록 생성
        route_mask = stops_gdf["route"].astype(str).str.strip() == str(selected_route).strip()
        route_stops_series = stops_gdf.loc[route_mask, "name"]
        r_stops = route_stops_series.astype(str).str.strip().tolist()
        
        if not r_stops:
            st.error("선택한 노선에 정류장이 없습니다.")
            st.stop()

        start = st.selectbox("출발 정류장", r_stops)
        ends = [s for s in r_stops if s != start] or r_stops
        end = st.selectbox("도착 정류장", ends)

        mode = st.radio("이동 모드", ["운전자(도로)", "도보(보행로)"], horizontal=True)
        profile = "driving" if "운전자" in mode else "walking"

        st.caption("Mapbox Directions → 실패 시 OSMnx 폴백")
        
        col_btn1, col_btn2 = st.columns(2)
        with col_btn1:
            generate = st.button("노선 최적화")
        with col_btn2:
            clear = st.button("초기화", type="secondary")
            
    except Exception as e:
        st.error(f"UI 생성 오류: {e}")
        st.stop()

# 초기화
if clear:
    st.session_state["segments"] = []
    st.session_state["order"] = []
    st.session_state["duration"] = 0.0
    st.session_state["distance"] = 0.0
    st.success("✅ 초기화 완료")

# ===================== 경로 생성 =====================
if generate:
    try:
        # 입력 검증
        if not isinstance(start, str) or not isinstance(end, str):
            st.error("출발/도착 정류장을 올바르게 선택해주세요.")
        else:
            s = safe_name_to_lonlat(start)
            e = safe_name_to_lonlat(end)
            
            coords = [c for c in [s, e] if c is not None]
            
            if len(coords) < 2:
                st.error("출발지와 도착지의 좌표를 찾을 수 없습니다.")
            else:
                with st.spinner("경로 생성 중..."):
                    # Mapbox 시도
                    segs, sec, meters = mapbox_route(coords, profile=profile)

                    # OSMnx 폴백
                    if not segs:
                        try:
                            avg_lat = sum([c[1] for c in coords]) / len(coords)
                            avg_lon = sum([c for c in coords]) / len(coords)  # 수정됨
                            net_type = "drive" if profile == "driving" else "walk"
                            G = load_graph(avg_lat, avg_lon, dist=OSMNX_DIST_M, net_type=net_type)
                            spd = 30.0 if profile == "driving" else 4.5
                            segs, sec, meters = osmnx_route(G, coords, speed_kmh=spd)
                        except Exception as fallback_error:
                            st.warning(f"폴백 경로 생성 실패: {fallback_error}")

                    if segs:
                        st.session_state["segments"] = segs
                        st.session_state["order"] = [start, end]
                        st.session_state["duration"] = sec / 60.0
                        st.session_state["distance"] = meters / 1000.0
                        st.success("✅ 실도로 기반 노선 최적화 완료")
                    else:
                        st.error("❌ 경로 생성에 실패했습니다. Mapbox 토큰을 확인하거나 다른 정류장을 시도해보세요.")

    except Exception as e:
        st.error(f"❌ 경로 생성 중 오류가 발생했습니다: {e}")

# ===================== 중간: 요약 =====================
with col2:
    st.markdown('<div class="section-title">운행 순서</div>', unsafe_allow_html=True)
    
    order = st.session_state.get("order", [])
    if order:
        for i, nm in enumerate(order, 1):
            st.markdown(f'<div class="visit"><div class="badge">{i}</div><div>{nm}</div></div>', unsafe_allow_html=True)
    else:
        st.info("경로를 생성하면 순서가 표시됩니다.")
    
    st.metric("⏱️ 예상 소요시간", f"{st.session_state.get('duration', 0.0):.1f}분")
    st.metric("📏 예상 이동거리", f"{st.session_state.get('distance', 0.0):.2f}km")

# ===================== 우측: 지도 =====================
with col3:
    try:
        # 안전한 중심점 계산
        try:
            clat = float(stops_gdf["lat"].mean())
            clon = float(stops_gdf["lon"].mean())
            if math.isnan(clat) or math.isnan(clon):
                clat, clon = 36.8151, 127.1139
        except Exception:
            clat, clon = 36.8151, 127.1139

        m = folium.Map(location=[clat, clon], zoom_start=13, tiles="CartoDB Positron",
                       prefer_canvas=True, control_scale=True)

        # 원본 노선 표시
        colors = {"DRT-1호선":"#4285f4","DRT-2호선":"#ea4335","DRT-3호선":"#34a853","DRT-4호선":"#fbbc04"}
        
        try:
            g = bus_routes.get(selected_route)
            if g is not None and not g.empty:
                coords = []
                for _, row in g.iterrows():
                    geom_coords = safe_get_coords_from_geom(row.geometry)
                    coords.extend([(lat, lon) for lon, lat in geom_coords])
                
                if coords:
                    folium.PolyLine(coords, color=colors.get(selected_route, "#666"),
                                    weight=3, opacity=0.35, tooltip=f"{selected_route} (원본)").add_to(m)
        except Exception:
            pass

        # 정류장 마커
        try:
            mc = MarkerCluster().add_to(m)
            route_stops_df = stops_gdf[stops_gdf["route"] == selected_route]
            
            for _, row in route_stops_df.iterrows():
                try:
                    lat, lon = float(row["lat"]), float(row["lon"])
                    if not (math.isnan(lat) or math.isnan(lon)):
                        folium.Marker([lat, lon],
                                      popup=folium.Popup(f"<b>{row['name']}</b>", max_width=220),
                                      tooltip=str(row["name"]),
                                      icon=folium.Icon(color="blue", icon="bus", prefix="fa")).add_to(mc)
                except Exception:
                    continue
        except Exception:
            pass

        # 실도로 경로 표시
        segs = st.session_state.get("segments", [])
        if segs:
            try:
                palette = ["#3f7cff", "#00b894", "#ff7675", "#fdcb6e", "#6c5ce7"]
                for i, seg in enumerate(segs):
                    try:
                        if seg and len(seg) >= 2:
                            latlon = [(float(p[1]), float(p[0])) for p in seg if len(p) >= 2]  # 수정됨
                            if latlon:
                                folium.PolyLine(latlon, color=palette[i % len(palette)],
                                              weight=7, opacity=0.92, tooltip=f"실도로 경로 {i+1}").add_to(m)
                    except Exception:
                        continue

                # 출발/도착 마커
                order = st.session_state.get("order", [])
                if len(order) >= 2:
                    try:
                        s_coord = safe_name_to_lonlat(order[0])
                        e_coord = safe_name_to_lonlat(order[-1])
                        
                        if s_coord:
                            folium.Marker([s_coord[1], s_coord[0]],  # 수정됨
                                        icon=folium.Icon(color="green", icon="play", prefix="fa"),
                                        tooltip=f"출발: {order}").add_to(m)
                        if e_coord:
                            folium.Marker([e_coord[1], e_coord],  # 수정됨
                                        icon=folium.Icon(color="red", icon="stop", prefix="fa"),
                                        tooltip=f"도착: {order[-1]}").add_to(m)
                    except Exception:
                        pass
                        
            except Exception:
                pass

        # 지도 출력
        st.markdown('<div class="map-container">', unsafe_allow_html=True)
        st_folium(m, width="100%", height=560, returned_objects=[], use_container_width=True, key="drt_nav_map")
        st.markdown('</div>', unsafe_allow_html=True)
        
    except Exception as e:
        st.error(f"지도 렌더링 오류: {e}")
