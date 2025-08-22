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

# ---------------------------
# 환경변수/토큰 (환경변수 우선)
# ---------------------------
MAPBOX_TOKEN = os.getenv("MAPBOX_TOKEN", "YOUR_MAPBOX_TOKEN_HERE")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")

# ---------------------------
# 안전한 데이터 로더
# ---------------------------
@st.cache_data
def load_data():
    try:
        gdf = gpd.read_file("cb_tour.shp").to_crs(epsg=4326)
        gdf["lon"], gdf["lat"] = gdf.geometry.x, gdf.geometry.y
        boundary = gpd.read_file("cb_shp.shp").to_crs(epsg=4326)
        data = pd.read_csv("cj_data_final.csv", encoding="cp949").drop_duplicates()
        return gdf, boundary, data
    except Exception as e:
        st.error(f"❌ 데이터 로드 실패: {str(e)}")
        return None, None, None

gdf, boundary, data = load_data()
if gdf is None or gdf.empty:
    st.stop()

# ---------------------------
# 카페 정보 포맷
# ---------------------------
def format_cafes(cafes_df: pd.DataFrame) -> str:
    try:
        cafes_df = cafes_df.drop_duplicates(subset=["c_name", "c_value", "c_review"])
        if len(cafes_df) == 0:
            return ("현재 이 관광지 주변에 등록된 카페 정보는 없어요.\n"
                    "지도를 활용해 주변을 걸어보며 새로운 공간을 발견해보세요 😊")
        if len(cafes_df) == 1:
            row = cafes_df.iloc[0]
            rv = str(row.get("c_review", ""))
            if all(x not in rv for x in ["없음", "없읍"]):
                return f"**{row['c_name']}** (⭐ {row['c_value']})\n\"{rv}\""
            return f"**{row['c_name']}** (⭐ {row['c_value']})"
        grouped = cafes_df.groupby(["c_name", "c_value"])
        out = ["**주변의 평점 높은 카페들입니다!** 🌼\n"]
        for (name, value), group in grouped:
            reviews = [r for r in group["c_review"].dropna().unique()
                       if all(x not in str(r) for x in ["없음", "없읍"])]
            top_r = reviews[:3]
            if top_r:
                out.append(f"- **{name}** (⭐ {value})\n" + "\n".join([f"\"{r}\"" for r in top_r]))
            else:
                out.append(f"- **{name}** (⭐ {value})")
        return "\n\n".join(out)
    except Exception as e:
        return f"카페 정보 처리 중 오류가 발생했습니다: {str(e)}"

# ---------------------------
# 상태 기본값
# ---------------------------
DEFAULTS = {
    "order": [],
    "segments": [],
    "duration": 0.0,
    "distance": 0.0,
    "auto_gpt_input": "",
}
for k, v in DEFAULTS.items():
    if k not in st.session_state:
        st.session_state[k] = v

# ---------------------------
# 페이지 & 스타일 (empty selector 제거)
# ---------------------------
st.set_page_config(page_title="청풍로드 - 충청북도 맞춤형 AI기반 스마트 관광 가이드",
                   layout="wide", initial_sidebar_state="collapsed")
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Noto+Sans+KR:wght@300;400;500;600;700&display=swap');
html, body, [class*="css"] { font-family:'Noto Sans KR', -apple-system, BlinkMacSystemFont, sans-serif; }
.main > div { padding-top:1.2rem; padding-bottom:0.5rem; }
header[data-testid="stHeader"] { display:none; }
.stApp { background:#f8f9fa; }

/* 헤더 */
.header-container { display:flex; align-items:center; justify-content:center; gap:20px; margin-bottom:2rem; padding:1rem 0; }
.main-title { font-size:2.2rem; font-weight:700; color:#202124; margin:0; letter-spacing:-0.5px; }
.title-underline { width:100%; height:3px; background:linear-gradient(90deg,#4285f4,#34a853); margin:0 auto 1.4rem auto; border-radius:2px; }

/* 섹션 헤더 */
.section-header { font-size:1.15rem; font-weight:700; color:#1f2937; margin-bottom:14px; display:flex; align-items:center; gap:8px; padding-bottom:10px; border-bottom:2px solid #f3f4f6; }

/* 버튼 */
.stButton > button { background:linear-gradient(135deg,#667eea 0%,#764ba2 100%); color:#fff; border:none; border-radius:10px; padding:12px 20px; font-size:.9rem; font-weight:600; width:100%; height:44px; transition:.2s; box-shadow:0 4px 8px rgba(102,126,234,.3); }
.stButton > button:hover { transform:translateY(-2px); box-shadow:0 6px 16px rgba(102,126,234,.4); }

/* 순서 리스트 */
.visit-order-item { display:flex; align-items:center; padding:10px 14px; background:linear-gradient(135deg,#667eea 0%,#764ba2 100%); color:#fff; border-radius:10px; margin-bottom:8px; font-size:.9rem; font-weight:500; box-shadow:0 2px 4px rgba(102,126,234,.3); }
.visit-number { background:rgba(255,255,255,.9); color:#667eea; width:24px; height:24px; border-radius:50%; display:flex; align-items:center; justify-content:center; font-size:.8rem; font-weight:700; margin-right:10px; }

/* 메트릭 */
.stMetric { background:linear-gradient(135deg,#a8edea 0%,#fed6e3 100%); border:none; border-radius:12px; padding:14px 10px; text-align:center; box-shadow:0 2px 4px rgba(168,237,234,.3); }

/* 지도 컨테이너 */
.map-container { width:100%!important; height:520px!important; border-radius:12px!important; overflow:hidden!important; position:relative!important; background:transparent!important; border:2px solid #e5e7eb!important; }
div[data-testid="stIFrame"], div[data-testid="stIFrame"] > iframe,
.folium-map, .leaflet-container { width:100%!important; height:520px!important; border:none!important; border-radius:12px!important; background:transparent!important; }

/* 폼 */
.stTextInput > div > div > input, .stSelectbox > div > div > select {
  border:2px solid #e5e7eb; border-radius:8px; padding:10px 14px; font-size:.9rem; background:#fafafa;
}
.stTextInput > div > div > input:focus, .stSelectbox > div > div > select:focus {
  border-color:#667eea; background:#fff; box-shadow:0 0 0 3px rgba(102,126,234,.1);
}

/* 텍스트 가시성 */
.stSelectbox label, .stRadio label { color:#111 !important; opacity:1 !important; }
</style>
""", unsafe_allow_html=True)

# ---------------------------
# 헤더
# ---------------------------
st.markdown('''
<div class="header-container">
    <img src="https://raw.githubusercontent.com/JeongWon4034/cheongju/main/cheongpung_logo.png" alt="청풍로드 로고" style="width:94px;height:94px;">
    <div class="main-title">청풍로드 - 충청북도 맞춤형 AI기반 스마트 관광 가이드</div>
</div>
<div class="title-underline"></div>
''', unsafe_allow_html=True)

# ---------------------------
# 레이아웃
# ---------------------------
col1, col2, col3 = st.columns([1.5, 1.2, 3], gap="large")

# ---------------------------
# 좌측: 경로 설정
# ---------------------------
with col1:
    st.markdown('<div class="section-header">🚗 추천경로 설정</div>', unsafe_allow_html=True)

    st.markdown("**이동 모드**")
    mode = st.radio("", ["운전자", "도보"], horizontal=True, key="mode_key", label_visibility="collapsed")
    api_profile = "driving" if mode == "운전자" else "walking"

    places = gdf["name"].dropna().astype(str).unique().tolist()
    st.markdown("**출발지**")
    start = st.selectbox("", places, key="start_key", label_visibility="collapsed")

    st.markdown("**경유지**")
    waypoints = st.multiselect("", [p for p in places if p != start], key="wps_key", label_visibility="collapsed")

    st.markdown("**도착지**")
    dest_candidates = [p for p in places if p not in set([start] + waypoints)]
    end = st.selectbox("", dest_candidates if dest_candidates else places, key="end_key", label_visibility="collapsed")

    st.markdown("---")
    col_btn1, col_btn2 = st.columns(2)
    with col_btn1:
        optimize_clicked = st.button("경로 생성")
    with col_btn2:
        clear_clicked = st.button("초기화", type="secondary")

# 초기화
if clear_clicked:
    for k in ["order", "segments", "duration", "distance"]:
        st.session_state[k] = [] if k in ["order", "segments"] else 0.0
    for widget_key in ["mode_key", "start_key", "wps_key", "end_key"]:
        if widget_key in st.session_state:
            del st.session_state[widget_key]
    st.success("✅ 초기화가 완료되었습니다.")
    st.rerun()

# ---------------------------
# 경로 유틸
# ---------------------------
def name_to_lonlat(name: str):
    r = gdf[gdf["name"] == name]
    if r.empty or pd.isna(r.iloc[0]["lon"]) or pd.isna(r.iloc["lat"]):
        return None
    return float(r.iloc["lon"]), float(r.iloc["lat"])

def fetch_mapbox_route(coords_lonlat, profile="driving"):
    """
    coords_lonlat: [(lon, lat), (lon, lat), ...] 순서로 직렬 연결
    """
    try:
        if len(coords_lonlat) < 2:
            return [], 0.0, 0.0
        segs, total_sec, total_m = [], 0.0, 0.0
        for i in range(len(coords_lonlat) - 1):
            x1, y1 = coords_lonlat[i]
            x2, y2 = coords_lonlat[i + 1]
            url = f"https://api.mapbox.com/directions/v5/mapbox/{profile}/{x1},{y1};{x2},{y2}"
            params = {"geometries": "geojson", "overview": "full", "access_token": MAPBOX_TOKEN}
            r = requests.get(url, params=params, timeout=10)
            if r.status_code == 200:
                js = r.json()
                if js.get("routes"):
                    route = js["routes"][0]
                    segs.append(route["geometry"]["coordinates"])  # [[lon,lat],...]
                    total_sec += route.get("duration", 0.0)
                    total_m += route.get("distance", 0.0)
                else:
                    st.warning(f"구간 {i+1}의 경로를 찾지 못했습니다.")
            else:
                st.warning(f"Mapbox 호출 실패 {r.status_code}")
        return segs, total_sec, total_m
    except Exception as e:
        st.warning(f"경로 요청 오류: {str(e)}")
        return [], 0.0, 0.0

# ---------------------------
# 경로 생성
# ---------------------------
if optimize_clicked:
    try:
        order_names = [start] + waypoints + [end]
        # 중복 제거(연속 중복만)
        compact = [order_names[0]]
        for n in order_names[1:]:
            if n != compact[-1]:
                compact.append(n)
        # 이름 → 좌표
        coords = []
        for nm in compact:
            ll = name_to_lonlat(nm)
            if ll is None:
                st.warning(f"'{nm}' 좌표를 찾지 못했습니다.")
                continue
            coords.append(ll)
        # 최소 2개 보장
        if len(coords) == 1:
            x, y = coords
            coords.append((x + 0.0005, y))
        segs, sec, m = fetch_mapbox_route(coords, api_profile)
        if segs:
            st.session_state["order"] = compact
            st.session_state["segments"] = segs
            st.session_state["duration"] = sec / 60.0
            st.session_state["distance"] = m / 1000.0
            st.success("✅ 경로 생성 완료")
            st.rerun()
        else:
            st.error("❌ 경로를 생성하지 못했습니다. 다른 장소 조합을 시도해 보세요.")
    except Exception as e:
        st.error(f"경로 생성 오류: {str(e)}")

# ---------------------------
# 중간: 요약/순서/메트릭
# ---------------------------
with col2:
    st.markdown('<div class="section-header">📍 방문 순서</div>', unsafe_allow_html=True)
    if st.session_state.get("order"):
        for i, name in enumerate(st.session_state["order"], 1):
            st.markdown(f"""
            <div class="visit-order-item">
                <div class="visit-number">{i}</div>
                <div>{name}</div>
            </div>
            """, unsafe_allow_html=True)
    else:
        st.markdown('<div class="visit-order-item" style="background:#e5e7eb;color:#111;">순서를 선택하고 경로를 생성하세요</div>', unsafe_allow_html=True)

    st.markdown("---")
    st.metric("⏱️ 예상 소요시간", f"{st.session_state.get('duration', 0.0):.1f}분")
    st.metric("📏 예상 이동거리", f"{st.session_state.get('distance', 0.0):.2f}km")

# ---------------------------
# 우측: 지도
# ---------------------------
with col3:
    st.markdown('<div class="section-header">🗺️ 지도</div>', unsafe_allow_html=True)
    try:
        clat, clon = float(gdf["lat"].mean()), float(gdf["lon"].mean())
        if math.isnan(clat) or math.isnan(clon):
            clat, clon = 36.6357, 127.4912  # 충북청주 근사
    except Exception:
        clat, clon = 36.6357, 127.4912

    m = folium.Map(location=[clat, clon], zoom_start=12, tiles="CartoDB Positron",
                   prefer_canvas=True, control_scale=True)

    # 경계선 (있으면)
    if boundary is not None and not boundary.empty:
        try:
            folium.GeoJson(boundary.to_json(), name="경계", style_function=lambda x: {
                "color": "#777", "weight": 1, "fill": False
            }).add_to(m)
        except Exception:
            pass

    # 관광지 마커
    mc = MarkerCluster().add_to(m)
    for _, row in gdf.iterrows():
        if pd.isna(row["lat"]) or pd.isna(row["lon"]):
            continue
        folium.Marker([row["lat"], row["lon"]],
                      popup=folium.Popup(f"<b>{row['name']}</b>", max_width=240),
                      tooltip=str(row["name"]),
                      icon=folium.Icon(color="green", icon="info-sign")).add_to(mc)

    # 최적화 경로 표시
    segments = st.session_state.get("segments", [])
    if segments:
        palette = ["#4285f4", "#34a853", "#ea4335", "#fbbc04", "#9c27b0", "#ff9800"]
        for i, seg in enumerate(segments):
            latlon = [(pt[1], pt) for pt in seg]  # [[lon,lat]] -> [(lat,lon)]
            folium.PolyLine(latlon, color=palette[i % len(palette)],
                            weight=6, opacity=0.8, tooltip=f"경로 {i+1}").add_to(m)
        # 순서 번호 배지(세그 중간)
        used = []
        for i, seg in enumerate(segments):
            if not seg:
                continue
            mid = seg[len(seg)//2]
            pos = [mid[1], mid]
            folium.map.Marker(
                pos,
                icon=DivIcon(html=f"<div style='background:{palette[i%len(palette)]};"
                                  "color:#fff;border-radius:50%;width:28px;height:28px;"
                                  "line-height:28px;text-align:center;font-weight:700;'>"
                                  f"{i+1}</div>")
            ).add_to(m)

    st.markdown('<div class="map-container">', unsafe_allow_html=True)
    st_folium(m, width="100%", height=520, returned_objects=[], use_container_width=True, key="cheongpung_map")
    st.markdown('</div>', unsafe_allow_html=True)
