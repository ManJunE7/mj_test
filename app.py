import os
import math
import time
import requests
import pandas as pd
import geopandas as gpd
import streamlit as st
import folium
from shapely.geometry import Point, LineString
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

# ===================== 안전한 유틸리티 함수 =====================
def haversine_m(lon1, lat1, lon2, lat2):
    """위경도 간 거리(미터) 계산"""
    try:
        R = 6371000.0
        dlon = math.radians(float(lon2) - float(lon1))
        dlat = math.radians(float(lat2) - float(lat1))
        a = math.sin(dlat/2)**2 + math.cos(math.radians(float(lat1)))*math.cos(math.radians(float(lat2)))*math.sin(dlon/2)**2
        return 2 * R * math.asin(math.sqrt(a))
    except (ValueError, TypeError):
        return 0.0

def ensure_exists(path):
    """파일 존재 확인"""
    if not os.path.exists(path):
        raise FileNotFoundError(f"파일 없음: {os.path.abspath(path)}")

def safe_extract_coords(geom):
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

# ===================== 견고한 데이터 로드 =====================
@st.cache_data
def load_drt_data():
    """DRT 셰이프파일 로드 및 정류장 생성"""
    bus_routes = {}
    all_stops = []
    
    for route_name, shp_path in ROUTE_FILES.items():
        try:
            ensure_exists(shp_path)
            gdf = gpd.read_file(shp_path).to_crs(epsg=4326)
            bus_routes[route_name] = gdf
            
            if gdf.empty:
                continue

            # 모든 지오메트리에서 좌표 추출
            all_coords = []
            for _, row in gdf.iterrows():
                coords = safe_extract_coords(row.geometry)
                all_coords.extend(coords)

            # 인접 중복 제거
            filtered_coords = []
            for lon, lat in all_coords:
                try:
                    lon, lat = float(lon), float(lat)
                    if math.isnan(lon) or math.isnan(lat):
                        continue
                    
                    if not filtered_coords:
                        filtered_coords.append((lon, lat))
                    else:
                        prev_lon, prev_lat = filtered_coords[-1]
                        if haversine_m(prev_lon, prev_lat, lon, lat) > MIN_GAP_M:
                            filtered_coords.append((lon, lat))
                except (ValueError, TypeError):
                    continue

            # 최소 2개 좌표 보장
            if len(filtered_coords) == 1:
                lon, lat = filtered_coords[0]
                dlat = FALLBACK_OFFSET_M / 111320.0
                filtered_coords.append((lon, lat + dlat))
            elif len(filtered_coords) == 0:
                # 기본 좌표 생성 (천안시 중심)
                base_lat, base_lon = 36.8151, 127.1139
                filtered_coords = [(base_lon, base_lat), (base_lon + 0.001, base_lat + 0.001)]

            # 정류장 생성
            for j, (lon, lat) in enumerate(filtered_coords, 1):
                all_stops.append({
                    "name": f"{route_name} {j}번 정류장",
                    "route": route_name,
                    "lon": float(lon),
                    "lat": float(lat),
                })
                
        except Exception as e:
            st.warning(f"{route_name} 로드 실패: {e}")
            continue

    if not all_stops:
        st.error("모든 노선 로드에 실패했습니다.")
        return None, None

    stops_df = pd.DataFrame(all_stops)
    stops_gdf = gpd.GeoDataFrame(
        stops_df, geometry=gpd.points_from_xy(stops_df.lon, stops_df.lat), crs="EPSG:4326"
    )
    stops_gdf["name"] = stops_gdf["name"].astype(str).str.strip()
    stops_gdf["route"] = stops_gdf["route"].astype(str).str.strip()
    
    return stops_gdf, bus_routes

# ===================== 안전한 좌표 검색 =====================
def safe_find_coordinates(stop_name, stops_gdf):
    """정류장명으로 좌표 검색"""
    try:
        if not stop_name:
            return None
            
        stop_name = str(stop_name).strip()
        matches = stops_gdf[stops_gdf["name"].astype(str).str.strip() == stop_name]
        
        if matches.empty:
            return None
            
        row = matches.reset_index(drop=True).iloc[0]
        lon, lat = float(row["lon"]), float(row["lat"])
        
        if math.isnan(lon) or math.isnan(lat):
            return None
            
        return (lon, lat)
        
    except Exception:
        return None

# ===================== 도로 그래프 로드 =====================
@st.cache_data
def load_road_graph(lat, lon, dist=OSMNX_DIST_M, network_type="drive"):
    """OSMnx 도로 그래프 로드"""
    try:
        return ox.graph_from_point((float(lat), float(lon)), dist=dist, network_type=network_type)
    except Exception:
        return None

# ===================== 향상된 Mapbox API 호출 =====================
def enhanced_mapbox_route(coord_pairs, profile="driving"):
    """고해상도 Mapbox 경로 요청"""
    segments, total_duration, total_distance = [], 0.0, 0.0
    
    if not coord_pairs or len(coord_pairs) < 2:
        return segments, total_duration, total_distance
        
    for i in range(len(coord_pairs) - 1):
        try:
            x1, y1 = float(coord_pairs[i][0]), float(coord_pairs[i][1])
            x2, y2 = float(coord_pairs[i + 1]), float(coord_pairs[i + 1][1])
            
            url = f"https://api.mapbox.com/directions/v5/mapbox/{profile}/{x1},{y1};{x2},{y2}"
            params = {
                "geometries": "geojson",
                "overview": "full",           # 고해상도 폴리라인
                "alternatives": "false",
                "continue_straight": "false", # 자연스러운 경로
                "access_token": MAPBOX_TOKEN
            }
            
            response = requests.get(url, params=params, timeout=15)
            
            if response.status_code == 200:
                data = response.json()
                if data.get("routes") and len(data["routes"]) > 0:
                    route = data["routes"][0]
                    coordinates = route["geometry"]["coordinates"]
                    
                    # 충분한 해상도 확인
                    if coordinates and len(coordinates) >= 2:
                        segments.append(coordinates)
                        total_duration += route.get("duration", 0.0)
                        total_distance += route.get("distance", 0.0)
                else:
                    st.warning(f"구간 {i+1}: 경로를 찾을 수 없음")
            else:
                st.warning(f"구간 {i+1}: API 오류 {response.status_code}")
                
        except Exception as e:
            st.warning(f"구간 {i+1}: 요청 실패 - {e}")
            continue
            
    return segments, total_duration, total_distance

# ===================== 향상된 OSMnx 폴백 =====================
def enhanced_osmnx_route(graph, coord_pairs, speed_kmh=30.0):
    """실도로 기반 OSMnx 경로 생성"""
    if not graph or len(coord_pairs) < 2:
        return [], 0.0, 0.0
        
    # 정류장을 도로 노드에 스냅
    snapped_nodes = []
    for lon, lat in coord_pairs:
        try:
            nearest_node = ox.distance.nearest_nodes(graph, float(lon), float(lat))
            snapped_nodes.append(nearest_node)
        except Exception:
            return [], 0.0, 0.0

    if len(snapped_nodes) < 2:
        return [], 0.0, 0.0

    route_segments = []
    total_length = 0.0
    
    for i in range(len(snapped_nodes) - 1):
        try:
            # 최단 경로 계산
            path = ox.shortest_path(graph, snapped_nodes[i], snapped_nodes[i + 1], weight="length")
            if not path or len(path) < 2:
                continue
                
            # 에지 지오메트리 추출 (핵심 개선!)
            edge_coords = []
            for j in range(len(path) - 1):
                u, v = path[j], path[j + 1]
                edge_data = graph.get_edge_data(u, v)
                
                if edge_data:
                    # 첫 번째 에지 선택
                    edge_info = list(edge_data.values())[0]
                    if 'geometry' in edge_info and edge_info['geometry'] is not None:
                        # 실제 도로 곡선 사용
                        geom = edge_info['geometry']
                        edge_coords.extend(list(geom.coords))
                    else:
                        # geometry가 없으면 노드 좌표 사용
                        u_coords = [graph.nodes[u]['x'], graph.nodes[u]['y']]
                        v_coords = [graph.nodes[v]['x'], graph.nodes[v]['y']]
                        edge_coords.extend([u_coords, v_coords])
                        
            if edge_coords:
                route_segments.append(edge_coords)
                
                # 거리 계산
                edge_lengths = ox.utils_graph.get_route_edge_attributes(graph, path, "length")
                if isinstance(edge_lengths, list):
                    total_length += sum([l for l in edge_lengths if l is not None])
                elif edge_lengths is not None:
                    total_length += float(edge_lengths)
                    
        except Exception as e:
            st.warning(f"OSMnx 경로 계산 실패: {e}")
            continue
            
    # 시간 계산
    total_time = (total_length / (speed_kmh * 1000 / 3600)) if speed_kmh > 0 else 0.0
    return route_segments, total_time, total_length

# ===================== Streamlit UI =====================
st.set_page_config(
    page_title="천안 DRT - 실도로 기반 최적 경로", 
    layout="wide", 
    initial_sidebar_state="collapsed"
)

st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Noto+Sans+KR:wght@300;400;500;600;700&display=swap');
html, body, [class*="css"] { font-family:'Noto Sans KR', -apple-system, BlinkMacSystemFont, sans-serif; }
header[data-testid="stHeader"] { display:none; }
.stApp { background:#f8f9fa; }
.section-title { font-size:1.2rem; font-weight:700; color:#1f2937; margin:0.8rem 0 0.5rem 0; }
.map-container { width:100%!important; height:580px!important; border-radius:12px!important; border:2px solid #e5e7eb!important; overflow:hidden!important; }
div[data-testid="stIFrame"], div[data-testid="stIFrame"] > iframe,
.folium-map, .leaflet-container { width:100%!important; height:580px!important; border:none!important; border-radius:12px!important; background:transparent!important; }
.route-item { display:flex; align-items:center; gap:8px; background:#667eea; color:#fff; padding:8px 12px; border-radius:10px; margin-bottom:6px; font-size:0.9rem; }
.route-badge { background:#fff; color:#667eea; width:20px; height:20px; border-radius:50%; display:flex; align-items:center; justify-content:center; font-weight:700; font-size:0.75rem; }
</style>
""", unsafe_allow_html=True)

st.title("🚌 천안 DRT 실도로 기반 최적 경로")

# 데이터 로드
try:
    stops_gdf, bus_routes = load_drt_data()
    if stops_gdf is None:
        st.stop()
except Exception as e:
    st.error(f"❌ 데이터 로드 실패: {e}")
    st.stop()

# 세션 상태 초기화
for key, default_value in {"segments": [], "order": [], "duration": 0.0, "distance": 0.0}.items():
    if key not in st.session_state:
        st.session_state[key] = default_value

# 레이아웃
col1, col2, col3 = st.columns([1.4, 1.2, 3], gap="large")

# ===================== 좌측: 설정 패널 =====================
with col1:
    st.markdown('<div class="section-title">🚌 운행 설정</div>', unsafe_allow_html=True)
    
    # 노선 선택
    route_names = list(bus_routes.keys())
    selected_route = st.selectbox("노선 선택", route_names, key="route_select")
    
    # 해당 노선의 정류장 목록
    route_stops = stops_gdf[stops_gdf["route"] == selected_route]["name"].astype(str).tolist()
    
    if not route_stops:
        st.error("선택한 노선에 정류장이 없습니다.")
        st.stop()
    
    # 출발지/도착지 선택
    start_stop = st.selectbox("출발 정류장", route_stops, key="start_select")
    available_destinations = [s for s in route_stops if s != start_stop] or route_stops
    end_stop = st.selectbox("도착 정류장", available_destinations, key="end_select")
    
    # 이동 모드
    travel_mode = st.radio("이동 모드", ["운전자(도로)", "보행자(보행로)"], horizontal=True)
    api_profile = "driving" if "운전자" in travel_mode else "walking"
    
    st.markdown("---")
    
    # 버튼
    col_btn1, col_btn2 = st.columns(2)
    with col_btn1:
        generate_route = st.button("🗺️ 경로 생성", use_container_width=True)
    with col_btn2:
        clear_route = st.button("🔄 초기화", type="secondary", use_container_width=True)

# 초기화 처리
if clear_route:
    for key in ["segments", "order", "duration", "distance"]:
        st.session_state[key] = [] if key in ["segments", "order"] else 0.0
    st.success("✅ 초기화 완료")
    st.rerun()

# ===================== 경로 생성 로직 =====================
if generate_route:
    # 입력 검증
    if not isinstance(start_stop, str) or not isinstance(end_stop, str):
        st.error("출발/도착 정류장을 올바르게 선택해주세요.")
    else:
        # 좌표 검색
        start_coords = safe_find_coordinates(start_stop, stops_gdf)
        end_coords = safe_find_coordinates(end_stop, stops_gdf)
        
        if not start_coords or not end_coords:
            st.error("출발지 또는 도착지의 좌표를 찾을 수 없습니다.")
        else:
            coordinates = [start_coords, end_coords]
            
            # 진행 상황 표시
            progress_container = st.container()
            with progress_container:
                progress_bar = st.progress(0)
                status_text = st.empty()
                
                # 1단계: Mapbox API 호출
                status_text.text("🛣️ Mapbox 실도로 경로 요청 중...")
                progress_bar.progress(30)
                
                segments, duration_sec, distance_m = enhanced_mapbox_route(coordinates, api_profile)
                
                # 2단계: OSMnx 폴백 (필요시)
                if not segments:
                    status_text.text("🌐 OSMnx 도로망 기반 경로 계산 중...")
                    progress_bar.progress(60)
                    
                    try:
                        avg_lat = (start_coords[1] + end_coords[1]) / 2
                        avg_lon = (start_coords + end_coords) / 2
                        network_type = "drive" if api_profile == "driving" else "walk"
                        
                        road_graph = load_road_graph(avg_lat, avg_lon, dist=OSMNX_DIST_M, network_type=network_type)
                        
                        if road_graph:
                            speed = 30.0 if api_profile == "driving" else 4.5
                            segments, duration_sec, distance_m = enhanced_osmnx_route(road_graph, coordinates, speed_kmh=speed)
                        else:
                            st.warning("도로 그래프 로드 실패")
                            
                    except Exception as fallback_error:
                        st.warning(f"폴백 경로 생성 실패: {fallback_error}")
                
                # 3단계: 결과 저장
                status_text.text("✅ 경로 생성 완료!")
                progress_bar.progress(100)
                
                if segments:
                    st.session_state["segments"] = segments
                    st.session_state["order"] = [start_stop, end_stop]
                    st.session_state["duration"] = duration_sec / 60.0
                    st.session_state["distance"] = distance_m / 1000.0
                    
                    time.sleep(0.8)
                    progress_bar.empty()
                    status_text.empty()
                    
                    st.success("✅ 실도로 기반 경로가 성공적으로 생성되었습니다!")
                    st.rerun()
                else:
                    progress_bar.empty()
                    status_text.empty()
                    st.error("❌ 경로 생성에 실패했습니다. Mapbox 토큰을 확인하거나 다른 정류장을 시도해보세요.")

# ===================== 중간: 결과 요약 =====================
with col2:
    st.markdown('<div class="section-title">📍 운행 정보</div>', unsafe_allow_html=True)
    
    # 운행 순서
    order = st.session_state.get("order", [])
    if order:
        for idx, stop_name in enumerate(order, 1):
            st.markdown(
                f'<div class="route-item">'
                f'<div class="route-badge">{idx}</div>'
                f'<div>{stop_name}</div>'
                f'</div>', 
                unsafe_allow_html=True
            )
    else:
        st.info("경로를 생성하면 운행 정보가 표시됩니다.")
    
    # 메트릭
    st.markdown("---")
    duration = st.session_state.get("duration", 0.0)
    distance = st.session_state.get("distance", 0.0)
    
    st.metric("⏱️ 예상 소요시간", f"{duration:.1f}분")
    st.metric("📏 예상 이동거리", f"{distance:.2f}km")
    
    if duration > 0:
        avg_speed = (distance / (duration / 60)) if duration > 0 else 0
        st.metric("⚡ 평균 속도", f"{avg_speed:.1f}km/h")

# ===================== 우측: 지도 시각화 =====================
with col3:
    try:
        # 중심점 계산
        center_lat = float(stops_gdf["lat"].mean())
        center_lon = float(stops_gdf["lon"].mean())
        
        if math.isnan(center_lat) or math.isnan(center_lon):
            center_lat, center_lon = 36.8151, 127.1139
    except Exception:
        center_lat, center_lon = 36.8151, 127.1139

    # 지도 생성
    folium_map = folium.Map(
        location=[center_lat, center_lon], 
        zoom_start=13, 
        tiles="CartoDB Positron",
        prefer_canvas=True, 
        control_scale=True
    )

    # 원본 노선 표시
    route_colors = {
        "DRT-1호선": "#4285f4", 
        "DRT-2호선": "#ea4335", 
        "DRT-3호선": "#34a853", 
        "DRT-4호선": "#fbbc04"
    }
    
    try:
        selected_gdf = bus_routes.get(selected_route)
        if selected_gdf is not None and not selected_gdf.empty:
            route_coords = []
            for _, row in selected_gdf.iterrows():
                coords = safe_extract_coords(row.geometry)
                route_coords.extend([(lat, lon) for lon, lat in coords])
            
            if route_coords:
                folium.PolyLine(
                    route_coords, 
                    color=route_colors.get(selected_route, "#666"),
                    weight=3, 
                    opacity=0.4, 
                    tooltip=f"{selected_route} (원본 라인)"
                ).add_to(folium_map)
    except Exception:
        pass

    # 정류장 마커
    try:
        marker_cluster = MarkerCluster().add_to(folium_map)
        selected_stops = stops_gdf[stops_gdf["route"] == selected_route]
        
        for _, stop_row in selected_stops.iterrows():
            lat, lon = float(stop_row["lat"]), float(stop_row["lon"])
            if not (math.isnan(lat) or math.isnan(lon)):
                folium.Marker(
                    [lat, lon],
                    popup=folium.Popup(f"<b>{stop_row['name']}</b>", max_width=250),
                    tooltip=str(stop_row["name"]),
                    icon=folium.Icon(color="blue", icon="bus", prefix="fa")
                ).add_to(marker_cluster)
    except Exception:
        pass

    # 생성된 실도로 경로 표시
    segments = st.session_state.get("segments", [])
    if segments:
        try:
            route_palette = ["#ff5722", "#009688", "#3f51b5", "#9c27b0", "#795548"]
            
            for idx, segment in enumerate(segments):
                if segment and len(segment) >= 2:
                    # 좌표 변환: [lon, lat] → (lat, lon)
                    segment_coords = []
                    for point in segment:
                        if len(point) >= 2:
                            segment_coords.append((float(point[1]), float(point[0])))
                    
                    if segment_coords:
                        folium.PolyLine(
                            segment_coords, 
                            color=route_palette[idx % len(route_palette)],
                            weight=7, 
                            opacity=0.9, 
                            tooltip=f"실도로 경로 구간 {idx+1}"
                        ).add_to(folium_map)

            # 출발/도착 마커 강조
            order = st.session_state.get("order", [])
            if len(order) >= 2:
                try:
                    start_coords = safe_find_coordinates(order[0], stops_gdf)
                    end_coords = safe_find_coordinates(order[-1], stops_gdf)
                    
                    if start_coords:
                        folium.Marker(
                            [start_coords[1], start_coords[0]],
                            icon=folium.Icon(color="green", icon="play", prefix="fa"),
                            tooltip=f"🚌 출발: {order[0]}"
                        ).add_to(folium_map)
                        
                    if end_coords:
                        folium.Marker(
                            [end_coords[1], end_coords],
                            icon=folium.Icon(color="red", icon="stop", prefix="fa"),
                            tooltip=f"🏁 도착: {order[-1]}"
                        ).add_to(folium_map)
                except Exception:
                    pass
                    
        except Exception:
            pass

    # 지도 렌더링
    st.markdown('<div class="map-container">', unsafe_allow_html=True)
    st_folium(folium_map, width="100%", height=580, returned_objects=[], use_container_width=True, key="enhanced_drt_map")
    st.markdown('</div>', unsafe_allow_html=True)

# ===================== 하단 정보 =====================
st.markdown("---")
st.markdown("### 📊 시스템 정보")

info_col1, info_col2, info_col3, info_col4 = st.columns(4)
with info_col1:
    st.metric("🚌 총 노선 수", f"{len(bus_routes)}개")
with info_col2:
    st.metric("🚏 총 정류장", f"{len(stops_gdf)}개소")
with info_col3:
    total_segments = len(st.session_state.get("segments", []))
    st.metric("🛣️ 경로 구간", f"{total_segments}개")
with info_col4:
    route_type = "Mapbox API" if total_segments > 0 else "대기 중"
    st.metric("🌐 경로 타입", route_type)

# 사용 안내
with st.expander("📋 사용 안내", expanded=False):
    st.markdown("""
    **경로 생성 과정:**
    1. **노선 선택**: DRT-1~4호선 중 선택
    2. **정류장 선택**: 출발지와 도착지 선택
    3. **이동 모드**: 운전자(도로) 또는 보행자(보행로) 선택
    4. **경로 생성**: 실도로 기반 최적 경로 계산
    
    **기술 특징:**
    - 🛣️ **실도로 경로**: Mapbox Directions API로 실제 도로망을 따라 경로 생성
    - 🌐 **폴백 시스템**: API 실패 시 OSMnx로 자동 전환
    - 📍 **정류장 스냅핑**: 정류장을 실제 도로 노드에 정확히 배치
    - ⚡ **고해상도**: 직선이 아닌 상세한 곡선 경로 제공
    """)
