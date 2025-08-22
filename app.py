import streamlit as st
import geopandas as gpd
import pandas as pd
import folium
from folium.plugins import MarkerCluster, HeatMap
from folium.features import DivIcon
from shapely.geometry import Point
import osmnx as ox
import requests
from streamlit_folium import st_folium
import math
import os

# ────────────────────────────── 
# ✅ 환경변수 불러오기 (Streamlit Cloud 호환에 저장된 키 사용)
# ──────────────────────────────
MAPBOX_TOKEN = "pk.eyJ1IjoiZ3VyMDUxMDgiLCJhIjoiY21lZ2k1Y291MTdoZjJrb2k3bHc3cTJrbSJ9.DElgSQ0rPoRk1eEacPI8uQ"

# ──────────────────────────────
# ✅ 데이터 로드 (안전한 로드)
# ──────────────────────────────
@st.cache_data
def load_data():
    try:
        # DRT 노선별 데이터 로드
        bus_routes = {}
        all_stops = []
        
        for i in range(1, 5):
            try:
                route_data = gpd.read_file(f"./drt_{i}.shp").to_crs(epsg=4326)
                bus_routes[f"DRT-{i}호선"] = route_data
                
                # ✅ 수정된 geometry 접근 방식
                if not route_data.empty and len(route_data) > 0:
                    # 첫 번째 geometry 객체 가져오기
                    geom = route_data.geometry.iloc[0]
                    
                    # LineString인지 확인하고 coords 추출
                    if hasattr(geom, 'coords'):
                        coords = list(geom.coords)
                    elif hasattr(geom, 'geoms'):  # MultiLineString인 경우
                        coords = []
                        for line in geom.geoms:
                            coords.extend(list(line.coords))
                    else:
                        st.warning(f"DRT-{i}호선: 지원하지 않는 geometry 타입입니다.")
                        continue
                    
                    # 좌표에서 정류장 생성
                    for j, (lon, lat) in enumerate(coords):
                        all_stops.append({
                            'name': f"DRT-{i}호선 {j+1}번 정류장",
                            'route': f"DRT-{i}호선",
                            'lon': lon,
                            'lat': lat,
                            'stop_id': f"drt_{i}_{j+1}",
                            'zone': f"Zone-{((j//3)+1)}"
                        })
                        
            except Exception as route_error:
                st.warning(f"DRT-{i}호선 로드 실패: {str(route_error)}")
                continue
        
        # 정류장 DataFrame 생성
        if all_stops:
            stops_df = pd.DataFrame(all_stops)
            stops_gdf = gpd.GeoDataFrame(
                stops_df, 
                geometry=gpd.points_from_xy(stops_df.lon, stops_df.lat),
                crs="EPSG:4326"
            )
            stops_gdf["lon"], stops_gdf["lat"] = stops_gdf.geometry.x, stops_gdf.geometry.y
        else:
            stops_gdf = None
            
        return stops_gdf, bus_routes
        
    except Exception as e:
        st.error(f"❌ 데이터 로드 실패: {str(e)}")
        return None, None

gdf, bus_routes = load_data()

# 데이터 로드 실패 시 앱 중단
if gdf is None:
    st.stop()

# DRT 운행 정보 포맷 함수 (기존 카페 함수를 DRT용으로 수정)
def format_drt_info(route_df, stop_name):
    try:
        if route_df is None or route_df.empty:
            return ("현재 이 정류장에서 운행 중인 DRT 정보가 없습니다. \n"
                   "운행 시간표를 확인하거나 다른 정류장을 이용해보세요 😊")
        
        result = []
        result.append("**현재 운행 중인 DRT 노선 정보** 🚌\n")
        
        # 실시간 운행 정보 (샘플 데이터)
        drt_info = [
            {"vehicle_id": "DRT-01", "arrival": "3분 후", "passengers": "6/12명", "next_stops": "천안역, 시청"},
            {"vehicle_id": "DRT-02", "arrival": "8분 후", "passengers": "2/12명", "next_stops": "병원, 터미널"},
        ]
        
        for info in drt_info:
            result.append(f"- **{info['vehicle_id']}** ({info['arrival']} 도착예정) \n승객: {info['passengers']} | 경유: {info['next_stops']}")
        
        return "\n\n".join(result)
        
    except Exception as e:
        return f"DRT 정보 처리 중 오류가 발생했습니다: {str(e)}"

# ──────────────────────────────
# ✅ Session 초기화
# ──────────────────────────────
DEFAULTS = {
    "order": [],
    "segments": [],
    "duration": 0.0,
    "distance": 0.0,
    "messages": [{"role": "system", "content": "당신은 천안 DRT 운행 전문 관리자입니다."}],
    "auto_gpt_input": "",
    "selected_route": "DRT-1호선",
    "vehicle_count": 3,
    "vehicle_capacity": 12,
    "avg_wait_time": 8.5,
    "pickup_success_rate": 94.3,
    "vehicle_utilization": 78.2,
    "cost_efficiency": 1.25,
    "active_vehicles": [
        {"id": "DRT-01", "status": "운행중", "passengers": 6, "lat": 36.8151, "lon": 127.1139},
        {"id": "DRT-02", "status": "대기중", "passengers": 0, "lat": 36.8161, "lon": 127.1149},
        {"id": "DRT-03", "status": "운행중", "passengers": 3, "lat": 36.8141, "lon": 127.1129},
    ]
}

for k, v in DEFAULTS.items():
    if k not in st.session_state:
        st.session_state[k] = v

# ──────────────────────────────
# ✅ 페이지 설정 & 스타일
# ──────────────────────────────
st.set_page_config(
    page_title="천안 DRT 스마트 노선 최적화 시스템",
    layout="wide",
    initial_sidebar_state="collapsed"
)

st.markdown("""
<style>
/* 기본 폰트 시스템 */
@import url('https://fonts.googleapis.com/css2?family=Noto+Sans+KR:wght@300;400;500;600;700&display=swap');

html, body, [class*="css"] {
    font-family: 'Noto Sans KR', -apple-system, BlinkMacSystemFont, sans-serif;
}

/* 기본 스타일 */
.main > div {
    padding-top: 1.2rem;
    padding-bottom: 0.5rem;
}

header[data-testid="stHeader"] {
    display: none;
}

.stApp {
    background: #f8f9fa;
}

/* 헤더 컨테이너 (로고 + 제목) */
.header-container {
    display: flex;
    align-items: center;
    justify-content: center;
    gap: 20px;
    margin-bottom: 2rem;
    padding: 1rem 0;
}

.logo-image {
    width: 80px;
    height: 80px;
    object-fit: contain;
}

.main-title {
    font-size: 2.8rem;
    font-weight: 700;
    color: #202124;
    letter-spacing: -1px;
    margin: 0;
}

.title-underline {
    width: 100%;
    height: 3px;
    background: linear-gradient(90deg, #4285f4, #34a853);
    margin: 0 auto 2rem auto;
    border-radius: 2px;
}

/* 섹션 헤더 스타일 */
.section-header {
    font-size: 1.3rem;
    font-weight: 700;
    color: #1f2937;
    margin-bottom: 20px;
    display: flex;
    align-items: center;
    gap: 8px;
    padding-bottom: 12px;
    border-bottom: 2px solid #f3f4f6;
}

/* 버튼 스타일 개선 */
.stButton > button {
    background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
    color: white;
    border: none;
    border-radius: 10px;
    padding: 12px 20px;
    font-size: 0.9rem;
    font-weight: 600;
    width: 100%;
    height: 48px;
    transition: all 0.3s ease;
    box-shadow: 0 4px 8px rgba(102, 126, 234, 0.3);
}

.stButton > button:hover {
    transform: translateY(-2px);
    box-shadow: 0 6px 16px rgba(102, 126, 234, 0.4);
}

/* DRT 방문 순서 리스트 스타일 */
.visit-order-item {
    display: flex;
    align-items: center;
    padding: 12px 16px;
    background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
    color: white;
    border-radius: 12px;
    margin-bottom: 8px;
    font-size: 0.95rem;
    font-weight: 500;
    transition: all 0.2s ease;
    box-shadow: 0 2px 4px rgba(102, 126, 234, 0.3);
}

.visit-order-item:hover {
    transform: translateX(4px);
    box-shadow: 0 4px 8px rgba(102, 126, 234, 0.4);
}

.visit-number {
    background: rgba(255,255,255,0.9);
    color: #667eea;
    width: 28px;
    height: 28px;
    border-radius: 50%;
    display: flex;
    align-items: center;
    justify-content: center;
    font-size: 0.8rem;
    font-weight: 700;
    margin-right: 12px;
    flex-shrink: 0;
}

/* 차량 상태 아이템 스타일 */
.vehicle-status-item {
    display: flex;
    align-items: center;
    padding: 10px 14px;
    background: linear-gradient(135deg, #ff9a9e 0%, #fecfef 100%);
    color: #444;
    border-radius: 10px;
    margin-bottom: 6px;
    font-size: 0.9rem;
    font-weight: 500;
    transition: all 0.2s ease;
    box-shadow: 0 2px 4px rgba(255, 154, 158, 0.3);
}

.vehicle-status-item:hover {
    transform: translateX(3px);
    box-shadow: 0 4px 8px rgba(255, 154, 158, 0.4);
}

.vehicle-number {
    background: rgba(255,255,255,0.9);
    color: #ff6b6b;
    width: 24px;
    height: 24px;
    border-radius: 50%;
    display: flex;
    align-items: center;
    justify-content: center;
    font-size: 0.75rem;
    font-weight: 700;
    margin-right: 10px;
    flex-shrink: 0;
}

/* 메트릭 카드 스타일 */
.stMetric {
    background: linear-gradient(135deg, #a8edea 0%, #fed6e3 100%);
    border: none;
    border-radius: 12px;
    padding: 16px 10px;
    text-align: center;
    transition: all 0.2s ease;
    box-shadow: 0 2px 4px rgba(168, 237, 234, 0.3);
}

.stMetric:hover {
    transform: translateY(-2px);
    box-shadow: 0 4px 8px rgba(168, 237, 234, 0.4);
}

/* 빈 상태 메시지 */
.empty-state {
    text-align: center;
    padding: 40px 20px;
    color: #9ca3af;
    font-style: italic;
    font-size: 0.95rem;
    background: linear-gradient(135deg, #ffecd2 0%, #fcb69f 100%);
    border-radius: 12px;
    margin: 16px 0;
}

/* 🔧 지도 컨테이너 스타일 - 박스 제거 완전 수정 */
.map-container {
    width: 100% !important;
    height: 520px !important;
    border-radius: 12px !important;
    overflow: hidden !important;
    position: relative !important;
    background: transparent !important;
    border: 2px solid #e5e7eb !important;
    margin: 0 !important;
    padding: 0 !important;
    box-sizing: border-box !important;
}

/* Streamlit iframe 완전 초기화 */
div[data-testid="stIFrame"] {
    width: 100% !important;
    max-width: 100% !important;
    height: 520px !important;
    position: relative !important;
    overflow: hidden !important;
    box-sizing: border-box !important;
    border-radius: 12px !important;
    background: transparent !important;
    border: none !important;
    margin: 0 !important;
    padding: 0 !important;
}

div[data-testid="stIFrame"] > iframe {
    width: 100% !important;
    height: 100% !important;
    border: none !important;
    border-radius: 12px !important;
    max-width: 100% !important;
    box-sizing: border-box !important;
    background: transparent !important;
    margin: 0 !important;
    padding: 0 !important;
}

/* 🚨 핵심: Streamlit 내부 빈 div들 완전 제거 */
div[data-testid="stIFrame"] > iframe > html > body > div:empty {
    display: none !important;
}

div[data-testid="stIFrame"] div:empty {
    display: none !important;
}

/* 🚨 Folium 내부 빈 컨테이너 제거 */
.folium-map div:empty {
    display: none !important;
}

/* 🚨 Leaflet 오버레이 박스 제거 */
.leaflet-container .leaflet-control-container div:empty {
    display: none !important;
}

.leaflet-container > div:empty {
    display: none !important;
}

/* 🚨 모든 빈 오버레이 박스 강제 제거 */
div:empty:not(.leaflet-zoom-box):not(.leaflet-marker-icon):not(.leaflet-div-icon) {
    display: none !important;
}

/* 🚨 투명하거나 흰색 배경의 빈 박스들 제거 */
div[style*="background: white"]:empty,
div[style*="background: #fff"]:empty,
div[style*="background: #ffffff"]:empty,
div[style*="background-color: white"]:empty,
div[style*="background-color: #fff"]:empty,
div[style*="background-color: #ffffff"]:empty {
    display: none !important;
}

/* Folium/Leaflet 지도 자체 크기 제한 */
.folium-map {
    width: 100% !important;
    height: 100% !important;
    max-width: 100% !important;
    max-height: 520px !important;
    box-sizing: border-box !important;
    background: transparent !important;
    margin: 0 !important;
    padding: 0 !important;
    border: none !important;
}

/* Leaflet 컨테이너 크기 고정 */
.leaflet-container {
    width: 100% !important;
    height: 100% !important;
    max-width: 100% !important;
    max-height: 520px !important;
    box-sizing: border-box !important;
    background: transparent !important;
    margin: 0 !important;
    padding: 0 !important;
    border: none !important;
}

/* 폼 스타일 개선 */
.stTextInput > div > div > input,
.stSelectbox > div > div > select,
.stMultiSelect > div > div > div > div {
    border: 2px solid #e5e7eb;
    border-radius: 8px;
    padding: 10px 14px;
    font-size: 0.9rem;
    transition: all 0.2s ease;
    background: #fafafa;
}

.stTextInput > div > div > input:focus,
.stSelectbox > div > div > select:focus {
    border-color: #667eea;
    background: white;
    box-shadow: 0 0 0 3px rgba(102, 126, 234, 0.1);
}

/* 간격 조정 */
.block-container {
    padding-top: 1rem;
    padding-bottom: 1rem;
    max-width: 1400px;
}

/* 성공/경고 메시지 */
.stSuccess {
    background: linear-gradient(135deg, #d4edda 0%, #c3e6cb 100%);
    border: 1px solid #b8dacd;
    border-radius: 8px;
    color: #155724;
}

.stWarning {
    background: linear-gradient(135deg, #fff3cd 0%, #ffeaa7 100%);
    border: 1px solid #f8d7da;
    border-radius: 8px;
    color: #856404;
}

.stError {
    background: linear-gradient(135deg, #f8d7da 0%, #f5c6cb 100%);
    border: 1px solid #f1b0b7;
    border-radius: 8px;
    color: #721c24;
}

/* DRT 전용 추가 스타일 */
.drt-route-card {
    background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
    color: white;
    padding: 1rem;
    border-radius: 10px;
    margin: 0.5rem 0;
    box-shadow: 0 4px 8px rgba(102, 126, 234, 0.3);
}

.performance-metric {
    background: linear-gradient(135deg, #a8edea 0%, #fed6e3 100%);
    padding: 0.8rem;
    border-radius: 8px;
    text-align: center;
    margin: 0.3rem 0;
    font-weight: 600;
    color: #2d3748;
}

.time-slot-card {
    background: linear-gradient(135deg, #ffecd2 0%, #fcb69f 100%);
    padding: 0.6rem;
    border-radius: 6px;
    margin: 0.2rem 0;
    font-size: 0.9rem;
    color: #744210;
}
</style>
""", unsafe_allow_html=True)

# ──────────────────────────────
# ✅ 헤더 (DRT 로고)
# ──────────────────────────────
st.markdown('''
<div class="header-container">
    <div style="font-size: 80px;">🚌</div>
    <div class="main-title">천안 DRT 스마트 노선 최적화 시스템</div>
</div>
<div class="title-underline"></div>
''', unsafe_allow_html=True)

# ──────────────────────────────
# ✅ 메인 레이아웃 (3컬럼)
# ──────────────────────────────
col1, col2, col3 = st.columns([1.5, 1.2, 3], gap="large")

# ------------------------------
# ✅ [좌] DRT 설정 패널
# ------------------------------
with col1:
    st.markdown('<div class="section-header">🚌 DRT 운행 설정</div>', unsafe_allow_html=True)
    
    # 운행 시간대 설정
    st.markdown("**운행 시간대**")
    time_slot = st.selectbox("", ["오전 첫차 (06:00-09:00)", "오전 (09:00-12:00)", 
                                  "오후 (12:00-18:00)", "저녁 (18:00-21:00)"], 
                             key="time_slot_key", label_visibility="collapsed")
    
    # 노선 선택
    st.markdown("**운행 노선**")
    route_names = list(bus_routes.keys()) if bus_routes else ["DRT-1호선"]
    selected_route = st.selectbox("", route_names, key="route_key", label_visibility="collapsed")
    st.session_state["selected_route"] = selected_route
    
    # 해당 노선의 정류장 필터링
    if gdf is not None and not gdf.empty:
        route_stops = gdf[gdf["route"] == selected_route]["name"].tolist()
    else:
        route_stops = ["정류장 정보 없음"]
    
    if route_stops and route_stops[0] != "정류장 정보 없음":
        st.markdown("**출발 정류장**")
        start = st.selectbox("", route_stops, key="start_key", label_visibility="collapsed")
        
        st.markdown("**도착 정류장**")
        available_ends = [s for s in route_stops if s != start]
        end = st.selectbox("", available_ends, key="end_key", label_visibility="collapsed") if available_ends else start
        
        # 승차 시간
        st.markdown("**승차 시간**")
        pickup_time = st.time_input("", value=pd.to_datetime("07:30").time(), key="time_key", label_visibility="collapsed")
    else:
        st.warning("⚠️ 선택한 노선에 정류장 정보가 없습니다.")
        start = end = "정보 없음"
    
    st.markdown("---")
    
    # 차량 설정
    st.markdown("**투입 차량 수**")
    vehicle_count = st.slider("", 1, 10, st.session_state.get("vehicle_count", 3), key="vehicle_count_key", label_visibility="collapsed")
    st.session_state["vehicle_count"] = vehicle_count
    
    st.markdown("**차량당 승객 수**")
    vehicle_capacity = st.selectbox("", [8, 12, 15, 25], index=1, key="capacity_key", label_visibility="collapsed")
    st.session_state["vehicle_capacity"] = vehicle_capacity
    
    # 수요 예측 모드
    st.markdown("**수요 예측 모드**")
    demand_mode = st.radio("", ["실시간 수요", "과거 데이터 기반", "시뮬레이션"], key="demand_key", label_visibility="collapsed")
    
    st.markdown("---")
    
    col_btn1, col_btn2 = st.columns(2, gap="small")
    with col_btn1:
        optimize_clicked = st.button("노선 최적화")
    with col_btn2:
        simulate_clicked = st.button("운행 시뮬레이션")
    
    # 초기화 버튼
    if st.button("초기화", type="secondary"):
        clear_clicked = True
    else:
        clear_clicked = False

# ------------------------------
# ✅ 초기화 처리 개선
# ------------------------------
if clear_clicked:
    try:
        keys_to_clear = ["segments", "order", "duration", "distance", "auto_gpt_input"]
        for k in keys_to_clear:
            if k in st.session_state:
                if k in ["segments", "order"]:
                    st.session_state[k] = []
                elif k in ["duration", "distance"]:
                    st.session_state[k] = 0.0
                else:
                    st.session_state[k] = ""
        
        widget_keys = ["time_slot_key", "route_key", "start_key", "end_key", "time_key"]
        for widget_key in widget_keys:
            if widget_key in st.session_state:
                del st.session_state[widget_key]
        
        st.success("✅ 초기화가 완료되었습니다.")
        st.rerun()
    except Exception as e:
        st.error(f"❌ 초기화 중 오류: {str(e)}")

# ------------------------------
# ✅ [중간] DRT 성과 지표 및 운행 정보
# ------------------------------
with col2:
    st.markdown('<div class="section-header">📊 DRT 성과 지표</div>', unsafe_allow_html=True)
    
    # 실시간 KPI 메트릭
    col_kpi1, col_kpi2 = st.columns(2)
    with col_kpi1:
        st.metric("⏱️ 평균 대기시간", f"{st.session_state.get('avg_wait_time', 8.5):.1f}분")
        st.metric("🚌 차량 가동률", f"{st.session_state.get('vehicle_utilization', 78.2):.1f}%")
    
    with col_kpi2:
        st.metric("🎯 픽업 성공률", f"{st.session_state.get('pickup_success_rate', 94.3):.1f}%")
        st.metric("💰 운행 효율성", f"{st.session_state.get('cost_efficiency', 1.25):.2f}원/km")
    
    st.markdown("---")
    st.markdown('<div class="section-header">📍 운행 순서</div>', unsafe_allow_html=True)
    
    current_order = st.session_state.get("order", [])
    if current_order:
        for i, name in enumerate(current_order, 1):
            st.markdown(f'''
            <div class="visit-order-item">
                <div class="visit-number">{i}</div>
                <div>{name}</div>
            </div>
            ''', unsafe_allow_html=True)
        
        st.markdown("**운행 통계:**")
        st.metric("⏱️ 예상 소요시간", f"{st.session_state.get('duration', 0.0):.1f}분")
        st.metric("📏 예상 이동거리", f"{st.session_state.get('distance', 0.0):.2f}km")
    else:
        st.markdown('<div class="empty-state">노선 최적화 후 표시됩니다<br>🚌</div>', unsafe_allow_html=True)
    
    st.markdown("---")
    st.markdown("**현재 운행 중인 차량**")
    
    active_vehicles = st.session_state.get("active_vehicles", [])
    if active_vehicles:
        for i, vehicle in enumerate(active_vehicles, 1):
            status_icon = "🟢" if vehicle.get('status') == '운행중' else "🔵"
            st.markdown(f'''
            <div class="vehicle-status-item">
                <div class="vehicle-number">{i}</div>
                <div>{status_icon} {vehicle.get('id', 'Unknown')} - {vehicle.get('status', '대기중')} (승객 {vehicle.get('passengers', 0)}명)</div>
            </div>
            ''', unsafe_allow_html=True)
    else:
        st.markdown('<div class="empty-state">운행 중인 차량이 없습니다</div>', unsafe_allow_html=True)

# ------------------------------
# ✅ [우] 지도 시각화
# ------------------------------
with col3:
    st.markdown('<div class="section-header">🗺️ DRT 노선 및 실시간 현황</div>', unsafe_allow_html=True)
    
    # 지도 레이어 선택
    layer_options = ["모든 노선", "선택된 노선만", "정류장", "실시간 차량", "수요 밀집구역"]
    show_layers = st.multiselect(
        "표시할 레이어 선택:",
        layer_options,
        default=["선택된 노선만", "정류장"],
        key="layers_key"
    )
    
    # 지도 설정
    try:
        if gdf is not None and not gdf.empty:
            clat, clon = float(gdf["lat"].mean()), float(gdf["lon"].mean())
        else:
            clat, clon = 36.8151, 127.1139  # 천안 중심좌표
        
        if math.isnan(clat) or math.isnan(clon):
            clat, clon = 36.8151, 127.1139
    except Exception as e:
        st.warning(f"중심점 계산 오류: {str(e)}")
        clat, clon = 36.8151, 127.1139

    @st.cache_data
    def load_graph(lat, lon):
        try:
            return ox.graph_from_point((lat, lon), dist=3000, network_type="all")
        except Exception as e:
            st.warning(f"도로 네트워크 로드 실패: {str(e)}")
            try:
                return ox.graph_from_point((36.8151, 127.1139), dist=3000, network_type="all")
            except:
                return None

    G = load_graph(clat, clon)
    edges = None
    
    if G is not None:
        try:
            edges = ox.graph_to_gdfs(G, nodes=False)
        except Exception as e:
            st.warning(f"엣지 변환 실패: {str(e)}")

    # DRT 경로 스냅핑 (기존 로직 유지)
    if 'start' in locals() and 'end' in locals() and start != "정보 없음":
        stops = [start, end]
        snapped = []

        # 개선된 스냅핑
        try:
            for nm in stops:
                if gdf is not None:
                    matching_rows = gdf[gdf["name"] == nm]
                    if matching_rows.empty:
                        st.warning(f"⚠️ '{nm}' 정보를 찾을 수 없습니다.")
                        continue
                    
                    r = matching_rows.iloc[0]
                    
                    if pd.isna(r.lon) or pd.isna(r.lat):
                        st.warning(f"⚠️ '{nm}'의 좌표 정보가 없습니다.")
                        continue
                    
                    pt = Point(r.lon, r.lat)
                    
                    if edges is None or edges.empty:
                        snapped.append((r.lon, r.lat))
                        continue
                    
                    edges["d"] = edges.geometry.distance(pt)
                    if edges["d"].empty:
                        snapped.append((r.lon, r.lat))
                        continue
                    
                    ln = edges.loc[edges["d"].idxmin()]
                    sp = ln.geometry.interpolate(ln.geometry.project(pt))
                    snapped.append((sp.x, sp.y))
                    
        except Exception as e:
            st.error(f"❌ 지점 처리 중 오류: {str(e)}")
            snapped = []
            if gdf is not None:
                for nm in stops:
                    try:
                        r = gdf[gdf["name"] == nm].iloc[0]
                        if not (pd.isna(r.lon) or pd.isna(r.lat)):
                            snapped.append((r.lon, r.lat))
                    except Exception as coord_error:
                        st.warning(f"⚠️ '{nm}' 좌표를 가져올 수 없습니다: {str(coord_error)}")

    # 경로 생성 처리 (Mapbox API 사용)
    if optimize_clicked and 'snapped' in locals() and len(snapped) >= 2:
        try:
            segs, td, tl = [], 0.0, 0.0
            api_mode = "driving"  # DRT는 차량 운행
            
            for i in range(len(snapped) - 1):
                x1, y1 = snapped[i]
                x2, y2 = snapped[i + 1]
                coord = f"{x1},{y1};{x2},{y2}"
                
                url = f"https://api.mapbox.com/directions/v5/mapbox/{api_mode}/{coord}"
                params = {
                    "geometries": "geojson",
                    "overview": "full",
                    "access_token": MAPBOX_TOKEN
                }
                
                try:
                    r = requests.get(url, params=params, timeout=10)
                    if r.status_code == 200:
                        data_resp = r.json()
                        if data_resp.get("routes") and len(data_resp["routes"]) > 0:
                            route = data_resp["routes"][0]
                            segs.append(route["geometry"]["coordinates"])
                            td += route.get("duration", 0)
                            tl += route.get("distance", 0)
                        else:
                            st.warning(f"⚠️ 구간 {i+1}의 경로를 찾을 수 없습니다.")
                    else:
                        st.warning(f"⚠️ API 호출 실패 (상태코드: {r.status_code})")
                except requests.exceptions.Timeout:
                    st.warning("⚠️ API 호출 시간 초과")
                except Exception as api_error:
                    st.warning(f"⚠️ API 호출 오류: {str(api_error)}")
            
            if segs:
                st.session_state["order"] = stops
                st.session_state["duration"] = td / 60
                st.session_state["distance"] = tl / 1000
                st.session_state["segments"] = segs
                st.success("✅ DRT 노선 최적화가 완료되었습니다!")
                st.rerun()
            else:
                st.error("❌ 모든 구간의 경로 생성에 실패했습니다.")
                
        except Exception as e:
            st.error(f"❌ 경로 생성 중 오류 발생: {str(e)}")
            st.info("💡 다른 출발지나 도착지를 선택해보세요.")

    # 🔧 지도 렌더링 - 완전 수정된 구조
    try:
        m = folium.Map(
            location=[clat, clon], 
            zoom_start=13, 
            tiles="CartoDB Positron",
            prefer_canvas=True,
            control_scale=True
        )
        
        # DRT 노선 표시
        route_colors = {
            "DRT-1호선": "#4285f4",  # 파란색
            "DRT-2호선": "#ea4335",  # 빨간색  
            "DRT-3호선": "#34a853",  # 초록색
            "DRT-4호선": "#fbbc04"   # 노란색
        }
        
        # ✅ 올바른 노선 표시 로직
        for route_name, route_gdf in bus_routes.items():
            if route_gdf.empty:
                continue
                
            show_route = False
            if "모든 노선" in show_layers:
                show_route = True
            elif "선택된 노선만" in show_layers and route_name == selected_route:
                show_route = True
                
            if show_route:
                try:
                    if len(route_gdf) > 0:
                        geom = route_gdf.geometry.iloc[0]
                        
                        if hasattr(geom, 'coords'):
                            coords = [(lat, lon) for lon, lat in geom.coords]
                        elif hasattr(geom, 'geoms'):  # MultiLineString인 경우
                            coords = []
                            for line in geom.geoms:
                                coords.extend([(lat, lon) for lon, lat in line.coords])
                        else:
                            continue
                        
                        folium.PolyLine(
                            coords,
                            color=route_colors.get(route_name, "#666666"),
                            weight=5,
                            opacity=0.8,
                            tooltip=f"{route_name} 노선"
                        ).add_to(m)
                        
                except Exception as e:
                    st.warning(f"{route_name} 시각화 오류: {str(e)}")
        
        # 정류장 표시
        if "정류장" in show_layers and gdf is not None and not gdf.empty:
            mc = MarkerCluster().add_to(m)
            for _, row in gdf.iterrows():
                if not (pd.isna(row.lat) or pd.isna(row.lon)):
                    # 선택된 노선만 표시하는 경우 필터링
                    if "선택된 노선만" in show_layers and row["route"] != selected_route:
                        continue
                    
                    folium.Marker([row.lat, row.lon], 
                                popup=folium.Popup(f"<b>{row['name']}</b><br>{row['route']}", max_width=200),
                                tooltip=str(row["name"]),
                                icon=folium.Icon(color="blue", icon="bus", prefix="fa")).add_to(mc)
        
        # 최적화된 경로의 정류장 강조 표시
        if 'snapped' in locals() and snapped:
            current_order = st.session_state.get("order", stops if 'stops' in locals() else [])
            for idx, (x, y) in enumerate(snapped, 1):
                if idx <= len(current_order):
                    place_name = current_order[idx - 1]
                else:
                    place_name = f"정류장 {idx}"
                
                icon_color = "green" if idx == 1 else "red" if idx == len(snapped) else "orange"
                icon_name = "play" if idx == 1 else "stop" if idx == len(snapped) else "pause"
                
                folium.Marker([y, x], 
                            icon=folium.Icon(color=icon_color, icon=icon_name, prefix="fa"),
                            tooltip=f"{idx}. {place_name}",
                            popup=folium.Popup(f"<b>{idx}. {place_name}</b>", max_width=200)
                ).add_to(m)
        
        # 실시간 차량 위치 표시
        if "실시간 차량" in show_layers:
            for vehicle in st.session_state.get("active_vehicles", []):
                if 'lat' in vehicle and 'lon' in vehicle:
                    status_color = "red" if vehicle.get('status') == '운행중' else "gray"
                    folium.Marker(
                        [vehicle['lat'], vehicle['lon']],
                        popup=f"<b>{vehicle.get('id', 'Unknown')}</b><br>상태: {vehicle.get('status', '대기중')}<br>승객: {vehicle.get('passengers', 0)}명",
                        tooltip=f"{vehicle.get('id', 'Unknown')} (승객 {vehicle.get('passengers', 0)}명)",
                        icon=folium.Icon(color=status_color, icon="car", prefix="fa")
                    ).add_to(m)
        
        # 최적화된 경로 라인 표시
        if st.session_state.get("segments"):
            palette = ["#4285f4", "#34a853", "#ea4335", "#fbbc04", "#9c27b0", "#ff9800"]
            segments = st.session_state["segments"]
            
            used_positions = []
            min_distance = 0.001
            
            for i, seg in enumerate(segments):
                if seg:
                    folium.PolyLine([(pt[1], pt) for pt in seg], 
                                  color=palette[i % len(palette)], 
                                  weight=6, 
                                  opacity=0.8,
                                  tooltip=f"DRT 최적화 경로 {i+1}"
                    ).add_to(m)
                    
                    mid = seg[len(seg) // 2]
                    candidate_pos = [mid[1], mid]
                    
                    while any(abs(candidate_pos[0] - used) < min_distance and 
                            abs(candidate_pos[1] - used[1]) < min_distance 
                            for used in used_positions):
                        candidate_pos[0] += min_distance * 0.5
                        candidate_pos[1] += min_distance * 0.5
                    
                    folium.map.Marker(candidate_pos,
                        icon=DivIcon(html=f"<div style='background:{palette[i % len(palette)]};"
                                        "color:#fff;border-radius:50%;width:32px;height:32px;"
                                        "line-height:32px;text-align:center;font-weight:700;"
                                        "box-shadow:0 3px 6px rgba(0,0,0,0.4);'>"
                                        f"{i+1}</div>")
                    ).add_to(m)
                    
                    used_positions.append(candidate_pos)
            
            try:
                pts = [pt for seg in segments for pt in seg if seg]
                if pts:
                    m.fit_bounds([[min(p[1] for p in pts), min(p for p in pts)],
                                [max(p[1] for p in pts), max(p for p in pts)]])
            except:
                m.location = [clat, clon]
                m.zoom_start = 13
        else:
            m.location = [clat, clon]
            m.zoom_start = 13
        
        # 수요 밀집구역 표시 (히트맵)
        if "수요 밀집구역" in show_layers:
            # 샘플 수요 데이터 (실제로는 실시간 데이터 연동)
            heat_data = [
                [clat + 0.01, clon + 0.01, 0.8],
                [clat - 0.01, clon - 0.01, 0.6],
                [clat + 0.005, clon - 0.005, 0.9],
                [clat - 0.005, clon + 0.005, 0.7]
            ]
            HeatMap(heat_data, radius=15, blur=10, max_zoom=1).add_to(m)
        
        # 시뮬레이션 처리
        if simulate_clicked:
            st.info("🎮 DRT 운행 시뮬레이션이 시작되었습니다!")
            # 실제로는 여기서 시뮬레이션 로직 실행
            st.session_state["avg_wait_time"] = 7.2
            st.session_state["pickup_success_rate"] = 96.1
            st.session_state["vehicle_utilization"] = 82.5
        
        # 🔧 지도 컨테이너 - 완전 수정된 구조
        st.markdown('<div class="map-container">', unsafe_allow_html=True)
        map_data = st_folium(
            m,
            width="100%",
            height=520,
            returned_objects=[],  # 🚨 빈 객체 반환 방지
            use_container_width=True,
            key="drt_main_map"
        )
        st.markdown('</div>', unsafe_allow_html=True)
        
    except Exception as map_error:
        st.error(f"❌ 지도 렌더링 오류: {str(map_error)}")
        st.markdown('<div class="map-container" style="display: flex; align-items: center; justify-content: center; color: #6b7280;">DRT 지도를 불러올 수 없습니다.</div>', unsafe_allow_html=True)

# ──────────────────────────────
# ✅ 하단 DRT 통계 정보
# ──────────────────────────────
st.markdown("---")
st.markdown("### 📈 천안 DRT 운행 통계")

col_stat1, col_stat2, col_stat3, col_stat4 = st.columns(4)

with col_stat1:
    st.metric("📊 총 운행 노선", f"{len(bus_routes)}개 노선")
    
with col_stat2:
    total_stops = len(gdf) if gdf is not None and not gdf.empty else 0
    st.metric("🚏 총 정류장 수", f"{total_stops}개소")
    
with col_stat3:
    st.metric("🚐 운행 차량", f"{st.session_state.get('vehicle_count', 3)}대")
    
with col_stat4:
    capacity = st.session_state.get('vehicle_count', 3) * st.session_state.get('vehicle_capacity', 12)
    st.metric("👥 시간당 수용력", f"{capacity}명")

# 운행 효율성 분석
st.markdown("### 🎯 실시간 운행 효율성 분석")
col_analysis1, col_analysis2 = st.columns(2)

with col_analysis1:
    st.markdown("""
    **🟢 운행 성과:**
    - 평균 대기시간: 8.5분 (목표: 10분 이하)
    - 픽업 성공률: 94.3% (목표: 90% 이상)
    - 차량 가동률: 78.2% (목표: 75% 이상)
    """)

with col_analysis2:
    st.markdown("""
    **🔄 개선 포인트:**
    - 러시아워 차량 증편 검토
    - 수요 밀집구역 정류장 추가
    - 실시간 경로 조정 시스템 도입
    """)

# DRT 정보 표시 (기존 카페 함수를 DRT용으로 활용)
if 'selected_route' in locals() and bus_routes:
    selected_route_data = bus_routes.get(selected_route)
    drt_info = format_drt_info(selected_route_data, selected_route)
    with st.expander(f"📋 {selected_route} 상세 운행 정보", expanded=False):
        st.markdown(drt_info)
