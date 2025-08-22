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

# ──────────────────────────────
# ✅ 데이터 로드 (수정된 버전)
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
                
                # 각 노선의 정점들을 정류장으로 추출
                if not route_data.empty and hasattr(route_data.geometry.iloc[0], 'coords'):
                    coords = list(route_data.geometry.iloc.coords)
                    for j, (lon, lat) in enumerate(coords):
                        all_stops.append({
                            'name': f"DRT-{i}호선 {j+1}번 정류장",
                            'route': f"DRT-{i}호선",
                            'lon': lon,
                            'lat': lat,
                            'stop_id': f"drt_{i}_{j+1}"
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
        else:
            stops_gdf = None
            
        return stops_gdf, bus_routes
        
    except Exception as e:
        st.error(f"❌ 데이터 로드 실패: {str(e)}")
        return None, None

# ──────────────────────────────
# ✅ 페이지 설정
# ──────────────────────────────
st.set_page_config(
    page_title="천안 DRT 스마트 노선 최적화 시스템",
    layout="wide",
    initial_sidebar_state="collapsed"
)

# ──────────────────────────────
# ✅ 스타일링
# ──────────────────────────────
st.markdown("""
<style>
    .main > div {
        padding-top: 1rem;
    }
    .stButton > button {
        width: 100%;
        background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
        color: white;
        border: none;
        border-radius: 8px;
        padding: 0.5rem;
        font-weight: 600;
    }
    .metric-card {
        background: linear-gradient(135deg, #a8edea 0%, #fed6e3 100%);
        padding: 1rem;
        border-radius: 8px;
        text-align: center;
        margin: 0.5rem 0;
    }
    .route-item {
        background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
        color: white;
        padding: 0.5rem 1rem;
        margin: 0.3rem 0;
        border-radius: 6px;
        font-size: 0.9rem;
    }
</style>
""", unsafe_allow_html=True)

# ──────────────────────────────
# ✅ 헤더
# ──────────────────────────────
st.markdown("""
<div class="header-container" style="text-align:center; margin-bottom:2rem;">
    <h1 style="font-size:2.5rem; font-weight:700; color:#202124; margin:0;">
        🚌 천안 DRT 스마트 노선 최적화 시스템
    </h1>
    <p style="font-size:1.1rem; color:#5f6368; margin-top:0.5rem;">
        수요응답형 교통(Demand Responsive Transit) 실시간 운행 관리
    </p>
    <div style="width:100%; height:3px; background:linear-gradient(90deg, #4285f4, #34a853); margin:1rem auto; border-radius:2px;"></div>
</div>
""", unsafe_allow_html=True)

# ──────────────────────────────
# ✅ 데이터 로드
# ──────────────────────────────
stops, bus_routes = load_data()
if stops is None or not bus_routes:
    st.error("❌ DRT 데이터를 로드할 수 없습니다. 파일을 확인해주세요.")
    st.stop()

# ──────────────────────────────
# ✅ 세션 상태 초기화
# ──────────────────────────────
if "order" not in st.session_state:
    st.session_state["order"] = []
if "selected_route" not in st.session_state:
    st.session_state["selected_route"] = None
if "duration" not in st.session_state:
    st.session_state["duration"] = 0.0
if "distance" not in st.session_state:
    st.session_state["distance"] = 0.0

# ──────────────────────────────
# ✅ 레이아웃 (3컬럼)
# ──────────────────────────────
col1, col2, col3 = st.columns([1.3, 1.2, 3], gap="large")

# ------------------------------
# [좌] DRT 설정 패널
# ------------------------------
with col1:
    st.markdown("### 🚌 DRT 운행 설정")
    
    # 노선 선택
    route_names = list(bus_routes.keys())
    selected_route = st.selectbox("운행 노선", route_names)
    
    # 해당 노선의 정류장만 필터링
    route_stops = stops[stops["route"] == selected_route]["name"].tolist()
    
    if route_stops:
        start = st.selectbox("출발 정류장", route_stops)
        end = st.selectbox("도착 정류장", [s for s in route_stops if s != start])
        
        # 운행 시간대
        time_slot = st.selectbox(
            "운행 시간대", 
            ["오전 첫차 (06:00-09:00)", "오전 (09:00-12:00)", 
             "오후 (12:00-18:00)", "저녁 (18:00-21:00)"]
        )
        
        # 승차 시간
        pickup_time = st.time_input("승차 시간", value=pd.to_datetime("07:30").time())
        
        # 차량 설정
        st.markdown("---")
        vehicle_count = st.slider("투입 차량 수", 1, 8, 3)
        vehicle_capacity = st.selectbox("차량당 승객 수", [8, 12, 15, 25])
        
        # 버튼
        col_btn1, col_btn2 = st.columns(2)
        with col_btn1:
            optimize_clicked = st.button("노선 최적화")
        with col_btn2:
            simulate_clicked = st.button("운행 시뮬레이션")
            
        if st.button("초기화", type="secondary"):
            st.session_state["order"] = []
            st.session_state["duration"] = 0.0
            st.session_state["distance"] = 0.0
            st.rerun()
    else:
        st.warning("⚠️ 선택한 노선에 정류장 정보가 없습니다.")

# ------------------------------
# [중간] 성과 지표 및 운행 정보
# ------------------------------
with col2:
    st.markdown("### 📊 DRT 성과 지표")
    
    # 실시간 KPI
    col_metric1, col_metric2 = st.columns(2)
    with col_metric1:
        st.metric("⏱️ 평균 대기시간", f"{st.session_state.get('avg_wait_time', 8.5):.1f}분")
        st.metric("🚌 차량 가동률", f"{st.session_state.get('vehicle_utilization', 78.2):.1f}%")
    
    with col_metric2:
        st.metric("🎯 픽업 성공률", f"{st.session_state.get('pickup_success_rate', 94.3):.1f}%")
        st.metric("💰 운행 효율성", f"{st.session_state.get('cost_efficiency', 1.25):.2f}원/km")
    
    st.markdown("---")
    st.markdown("### 📍 운행 정보")
    
    if st.session_state["order"]:
        st.markdown("**경로:**")
        for i, stop_name in enumerate(st.session_state["order"], 1):
            st.markdown(f'<div class="route-item">#{i} {stop_name}</div>', unsafe_allow_html=True)
        
        st.markdown("**운행 통계:**")
        st.metric("⏱️ 예상 소요시간", f"{st.session_state['duration']:.1f}분")
        st.metric("📏 예상 이동거리", f"{st.session_state['distance']:.2f}km")
    else:
        st.info("노선 최적화 후 운행 정보가 표시됩니다")
    
    # 현재 운행 상태
    st.markdown("---")
    st.markdown("### 🚐 실시간 차량 현황")
    
    # 샘플 차량 상태 (실제로는 실시간 데이터를 연동)
    vehicles = [
        {"id": "DRT-01", "status": "운행중", "passengers": 6, "next_stop": "천안역"},
        {"id": "DRT-02", "status": "대기중", "passengers": 0, "next_stop": "차량기지"},
        {"id": "DRT-03", "status": "운행중", "passengers": 3, "next_stop": "시청"},
    ]
    
    for vehicle in vehicles:
        status_color = "🟢" if vehicle["status"] == "운행중" else "🔵"
        st.markdown(
            f"{status_color} **{vehicle['id']}** - {vehicle['status']} "
            f"(승객 {vehicle['passengers']}명) → {vehicle['next_stop']}"
        )

# ------------------------------
# [우] 지도 시각화
# ------------------------------
with col3:
    st.markdown("### 🗺️ DRT 노선 및 실시간 현황")
    
    # 지도 레이어 선택
    show_layers = st.multiselect(
        "표시할 레이어:",
        ["모든 노선", "선택된 노선만", "정류장", "실시간 차량", "수요 밀집구역"],
        default=["선택된 노선만", "정류장"]
    )
    
    # 지도 중심점 계산
    if not stops.empty:
        clat, clon = stops["lat"].mean(), stops["lon"].mean()
    else:
        clat, clon = 36.8151, 127.1139  # 천안 중심좌표
    
    # 지도 생성
    m = folium.Map(
        location=[clat, clon], 
        zoom_start=13, 
        tiles="CartoDB Positron",
        prefer_canvas=True
    )
    
    # 노선 표시
    route_colors = {
        "DRT-1호선": "#4285f4",  # 파란색
        "DRT-2호선": "#ea4335",  # 빨간색  
        "DRT-3호선": "#34a853",  # 초록색
        "DRT-4호선": "#fbbc04"   # 노란색
    }
    
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
                if hasattr(route_gdf.geometry.iloc[0], 'coords'):
                    coords = [(lat, lon) for lon, lat in route_gdf.geometry.iloc.coords]
                    folium.PolyLine(
                        coords,
                        color=route_colors.get(route_name, "#666666"),
                        weight=4,
                        opacity=0.8,
                        tooltip=f"{route_name} 노선"
                    ).add_to(m)
            except Exception as e:
                st.warning(f"{route_name} 시각화 오류: {str(e)}")
    
    # 정류장 표시
    if "정류장" in show_layers and not stops.empty:
        mc = MarkerCluster().add_to(m)
        
        for _, row in stops.iterrows():
            # 선택된 노선의 정류장만 표시하거나 모든 정류장 표시
            if "선택된 노선만" in show_layers and row["route"] != selected_route:
                continue
                
            icon_color = "blue"
            if "선택된 노선만" in show_layers:
                route_num = row["route"].split("-")[1][0]  # DRT-1호선 -> 1
                icon_color = ["blue", "red", "green", "orange"][int(route_num)-1]
            
            folium.Marker(
                [row["lat"], row["lon"]],
                popup=folium.Popup(f"<b>{row['name']}</b><br>{row['route']}", max_width=200),
                tooltip=row["name"],
                icon=folium.Icon(color=icon_color, icon="bus", prefix="fa")
            ).add_to(mc)
    
    # 실시간 차량 위치 (샘플 데이터)
    if "실시간 차량" in show_layers:
        sample_vehicles = [
            {"id": "DRT-01", "lat": clat + 0.01, "lon": clon + 0.01, "passengers": 6},
            {"id": "DRT-02", "lat": clat - 0.01, "lon": clon - 0.01, "passengers": 0},
            {"id": "DRT-03", "lat": clat + 0.005, "lon": clon - 0.005, "passengers": 3},
        ]
        
        for vehicle in sample_vehicles:
            folium.Marker(
                [vehicle["lat"], vehicle["lon"]],
                popup=f"<b>{vehicle['id']}</b><br>승객: {vehicle['passengers']}명",
                tooltip=f"{vehicle['id']} (승객 {vehicle['passengers']}명)",
                icon=folium.Icon(color="red", icon="car", prefix="fa")
            ).add_to(m)
    
    # 최적화/시뮬레이션 버튼 처리
    if optimize_clicked and 'start' in locals() and 'end' in locals():
        try:
            # 출발지/도착지 강조 표시
            start_row = stops[stops["name"] == start].iloc[0]
            end_row = stops[stops["name"] == end].iloc
            
            folium.Marker(
                [start_row.lat, start_row.lon], 
                icon=folium.Icon(color="green", icon="play", prefix="fa"),
                popup=f"<b>출발지</b><br>{start}"
            ).add_to(m)
            
            folium.Marker(
                [end_row.lat, end_row.lon], 
                icon=folium.Icon(color="red", icon="stop", prefix="fa"),
                popup=f"<b>도착지</b><br>{end}"
            ).add_to(m)
            
            # 세션 상태 업데이트 (실제로는 최적화 알고리즘 결과)
            st.session_state["order"] = [start, end]
            st.session_state["duration"] = 15.3  # 예시값
            st.session_state["distance"] = 7.2   # 예시값
            
            st.success("✅ 노선 최적화가 완료되었습니다!")
            st.rerun()
            
        except Exception as e:
            st.error(f"❌ 최적화 처리 중 오류: {str(e)}")
    
    if simulate_clicked:
        st.info("🎮 시뮬레이션이 시작되었습니다! (개발 중)")
    
    # 지도 표시
    try:
        st_folium(m, width="100%", height=520, key="drt_map")
    except Exception as map_error:
        st.error(f"❌ 지도 렌더링 오류: {str(map_error)}")

# ──────────────────────────────
# ✅ 하단 추가 정보
# ──────────────────────────────
st.markdown("---")
col_info1, col_info2, col_info3, col_info4 = st.columns(4)

with col_info1:
    st.metric("📊 총 운행 노선", len(bus_routes))
    
with col_info2:
    st.metric("🚏 총 정류장 수", len(stops) if not stops.empty else 0)
    
with col_info3:
    st.metric("🚐 운행 차량", f"{vehicle_count}대")
    
with col_info4:
    st.metric("👥 시간당 수용력", f"{vehicle_count * vehicle_capacity}명")
