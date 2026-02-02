import os
import time
import json
import math
import logging
import cloudscraper
import folium
from folium.plugins import Fullscreen, LocateControl, Draw
import requests
from datetime import datetime, timedelta

# ==========================================
# CONFIGURATION
# ==========================================
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger()

USER_ID = os.getenv("KOMOOT_USER_ID")
DATA_FILE = "all_points.json"

SIMPLIFY_FACTOR = int(os.getenv("SIMPLIFY_FACTOR", "2"))
TILE_ZOOM = 14
TILE_COLOR = "#FFA500"
TRACE_COLOR = "#0000FF"

# Estimation tuiles Z14 France Métropolitaine
TOTAL_TILES_FRANCE = 428000 

def get_scraper():
    return cloudscraper.create_scraper(browser={'browser': 'chrome','platform': 'windows','desktop': True})

def get_city_from_coords(lat, lon):
    url = "https://nominatim.openstreetmap.org/reverse"
    params = {"lat": lat, "lon": lon, "format": "json", "zoom": 10}
    headers = {'User-Agent': 'KomootSquadraMap/4.0'}
    try:
        time.sleep(1.1) 
        response = requests.get(url, params=params, headers=headers, timeout=5)
        if response.status_code == 200:
            addr = response.json().get('address', {})
            return addr.get('city') or addr.get('town') or addr.get('village') or addr.get('municipality') or "Inconnue"
    except Exception: pass
    return "Inconnue"

def fetch_public_tours_list(user_id):
    scraper = get_scraper()
    tours = []
    page = 0
    logger.info(f"📡 Récupération des activités pour {user_id}...")
    while True:
        url = f"https://api.komoot.de/v007/users/{user_id}/tours/"
        params = {'type': 'tour_recorded', 'sort': 'date', 'status': 'public', 'page': page, 'limit': 50}
        try:
            resp = scraper.get(url, params=params, timeout=15)
            if resp.status_code != 200: break
            data = resp.json()
            items = data.get('_embedded', {}).get('tours', [])
            if not items: break
            for t in items:
                tours.append({
                    'id': t['id'], 'name': t.get('name', 'Sans nom'),
                    'date': t.get('date'), 'distance': t.get('distance', 0),
                    'elevation_up': t.get('elevation_up', 0)
                })
            if page >= data.get('page', {}).get('totalPages', 0) - 1: break
            page += 1
            time.sleep(0.5)
        except Exception: break
    return tours

def fetch_tour_coordinates(tour_id):
    scraper = get_scraper()
    url = f"https://api.komoot.de/v007/tours/{tour_id}/coordinates"
    try:
        resp = scraper.get(url, timeout=10)
        if resp.status_code == 200:
            return [(item['lat'], item['lng']) for item in resp.json().get('items', [])]
    except Exception: pass
    return []

def deg2num(lat_deg, lon_deg, zoom):
    lat_rad = math.radians(lat_deg)
    n = 2.0 ** zoom
    xtile = int((lon_deg + 180.0) / 360.0 * n)
    ytile = int((1.0 - math.asinh(math.tan(lat_rad)) / math.pi) / 2.0 * n)
    return (xtile, ytile)

def num2deg(xtile, ytile, zoom):
    n = 2.0 ** zoom
    lon_deg = xtile / n * 360.0 - 180.0
    lat_rad = math.atan(math.sinh(math.pi * (1 - 2 * ytile / n)))
    return (math.degrees(lat_rad), lon_deg)

def load_data():
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, 'r') as f:
            db = json.load(f)
            if not isinstance(db.get("traces"), dict): db["traces"] = {}
            if not isinstance(db.get("tour_details"), dict): db["tour_details"] = {}
            return db
    return {"tour_details": {}, "traces": {}, "tiles": []}

def save_data(data):
    with open(DATA_FILE, 'w') as f:
        json.dump(data, f)

def update_database(user_id):
    db = load_data()
    online_tours = fetch_public_tours_list(user_id)
    existing_tiles = set(tuple(t) for t in db.get("tiles", []))
    
    count = 0
    for tour in online_tours:
        tid = str(tour['id'])
        if tid not in db["tour_details"] or tid not in db["traces"]:
            count += 1
            logger.info(f"🔄 Mise à jour sortie {tid} : {tour['name']}")
            points = fetch_tour_coordinates(tid)
            if points:
                city = get_city_from_coords(points[0][0], points[0][1])
                db["tour_details"][tid] = {
                    "id": tid, "name": tour['name'], "date": tour['date'],
                    "distance": tour['distance'], "elevation_up": tour['elevation_up'], "city": city
                }
                db["traces"][tid] = points[::SIMPLIFY_FACTOR]
                for lat, lon in db["traces"][tid]:
                    existing_tiles.add(deg2num(lat, lon, TILE_ZOOM))
            
            if count % 10 == 0: save_data(db)

    db["tiles"] = list(existing_tiles)
    save_data(db)
    create_map(db)

def create_map(db):
    m = folium.Map(location=[46.6, 2.2], zoom_start=6, tiles=None)
    
    folium.TileLayer('OpenStreetMap', name='Plan (OSM)').add_to(m)
    folium.TileLayer(
        tiles='https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',
        attr='Esri', name='Satellite (Aérien)', overlay=False
    ).add_to(m)
    
    folium.TileLayer(
        tiles='https://{s}.tile.openstreetmap.fr/osmfr/{z}/{x}/{y}.png',
        attr='OSM France', name='Noms des lieux', overlay=True, opacity=0.7
    ).add_to(m)

    tile_group = folium.FeatureGroup(name="Exploration (Tuiles)", show=True).add_to(m)
    for xtile, ytile in db.get("tiles", []):
        nw, se = num2deg(xtile, ytile, TILE_ZOOM), num2deg(xtile + 1, ytile + 1, TILE_ZOOM)
        folium.Rectangle(bounds=[[nw[0], nw[1]], [se[0], se[1]]], color=None, fill=True, fill_color=TILE_COLOR, fill_opacity=0.4, weight=0).add_to(tile_group)

    trace_group = folium.FeatureGroup(name="Parcours GPS", show=True).add_to(m)
    for tid, coords in db.get("traces", {}).items():
        info = db["tour_details"].get(tid, {})
        dist = round(info.get('distance', 0)/1000, 1)
        ele = info.get('elevation_up', 0)
        date = (info.get('date') or "2000-01-01")[:10]
        
        popup_html = f"<b>{info.get('name')}</b><br>📅 {date}<br>📏 {dist} km<br>⛰️ {ele} m D+"
        folium.PolyLine(coords, color=TRACE_COLOR, weight=3, opacity=0.7, 
                        tooltip=f"{info.get('name')} ({dist}km)",
                        popup=folium.Popup(popup_html, max_width=200)).add_to(trace_group)

    # ==========================================
    # OUTIL DE PLANIFICATION (SNAPPING + UNDO + GPX)
    # ==========================================
    Draw(
        export=False, 
        position='topleft',
        draw_options={
            'polyline': {'shapeOptions': {'color': '#00fbff', 'weight': 5}},
            'polygon': False, 'circle': False, 'marker': False, 'circlemarker': False, 'rectangle': False,
        }
    ).add_to(m)

    routing_js = """
    <script>
    document.addEventListener('DOMContentLoaded', function() {
        var map = null;
        for (var key in window) {
            if (key.startsWith('map_') && window[key] instanceof L.Map) {
                map = window[key];
                break;
            }
        }
        if (!map) return;

        var routePoints = [];
        var segments = []; // Stocke les coordonnées de chaque segment entre deux clics
        var startMarker = null;
        var routeLayer = L.polyline([], {color: '#00fbff', weight: 5, dashArray: '5, 10'}).addTo(map);

        var container = document.createElement('div');
        container.style.cssText = 'position:fixed; bottom:20px; left:50px; z-index:9999; display:flex; gap:10px; flex-wrap: wrap;';
        
        var exportBtn = document.createElement('button');
        exportBtn.innerHTML = '💾 Export GPX';
        exportBtn.style.cssText = 'background:#27ae60; color:white; border:none; padding:10px; border-radius:5px; cursor:pointer; font-weight:bold;';
        
        var undoBtn = document.createElement('button');
        undoBtn.innerHTML = '↩️ Annuler';
        undoBtn.style.cssText = 'background:#f39c12; color:white; border:none; padding:10px; border-radius:5px; cursor:pointer; font-weight:bold;';

        var resetBtn = document.createElement('button');
        resetBtn.innerHTML = '🗑️ Effacer';
        resetBtn.style.cssText = 'background:#e74c3c; color:white; border:none; padding:10px; border-radius:5px; cursor:pointer; font-weight:bold;';

        var distBadge = document.createElement('div');
        distBadge.innerHTML = '0.0 km';
        distBadge.style.cssText = 'background:white; padding:10px; border-radius:5px; border:1px solid #ccc; font-weight:bold; color: black;';

        container.appendChild(distBadge);
        container.appendChild(undoBtn);
        container.appendChild(exportBtn);
        container.appendChild(resetBtn);
        document.body.appendChild(container);

        function updateDistance() {
            var dist = 0;
            var flat = [].concat(...segments);
            for (var i = 0; i < flat.length - 1; i++) {
                dist += L.latLng(flat[i]).distanceTo(L.latLng(flat[i+1]));
            }
            distBadge.innerHTML = (dist / 1000).toFixed(1) + ' km';
        }

        function redraw() {
            var flat = [].concat(...segments);
            routeLayer.setLatLngs(flat);
            updateDistance();
        }

        map.on('click', function(e) {
            var newPoint = e.latlng;
            if (routePoints.length > 0) {
                var lastPoint = routePoints[routePoints.length - 1];
                fetch(`https://router.project-osrm.org/route/v1/foot/${lastPoint.lng},${lastPoint.lat};${newPoint.lng},${newPoint.lat}?overview=full&geometries=geojson`)
                    .then(res => res.json())
                    .then(data => {
                        if (data.routes && data.routes[0]) {
                            var coords = data.routes[0].geometry.coordinates.map(c => [c[1], c[0]]);
                            segments.push(coords);
                            routePoints.push(newPoint);
                            redraw();
                        }
                    });
            } else {
                routePoints.push(newPoint);
                segments.push([[newPoint.lat, newPoint.lng]]);
                startMarker = L.circleMarker(newPoint, {radius: 5, color: 'green', fillOpacity: 1}).addTo(map);
                updateDistance();
            }
        });

        undoBtn.onclick = function() {
            if (segments.length > 1) {
                segments.pop();
                routePoints.pop();
                redraw();
            } else if (segments.length === 1) {
                segments.pop();
                routePoints.pop();
                if(startMarker) map.removeLayer(startMarker);
                startMarker = null;
                redraw();
            }
        };

        resetBtn.onclick = function() {
            routePoints = []; segments = [];
            routeLayer.setLatLngs([]);
            distBadge.innerHTML = '0.0 km';
            if(startMarker) map.removeLayer(startMarker);
            startMarker = null;
            map.eachLayer(function(l) { if(l instanceof L.CircleMarker) map.removeLayer(l); });
        };

        exportBtn.onclick = function() {
            var flat = [].concat(...segments);
            if (flat.length < 2) return alert("Trace vide !");
            var gpx = '<?xml version="1.0" encoding="UTF-8"?><gpx version="1.1" creator="SquadraMap"><trk><name>Plan Squadra</name><trkseg>';
            flat.forEach(p => { gpx += `<trkpt lat="${p[0]}" lon="${p[1]}"></trkpt>`; });
            gpx += '</trkseg></trk></gpx>';
            var blob = new Blob([gpx], {type: 'application/gpx+xml'});
            var a = document.createElement('a');
            a.href = URL.createObjectURL(blob);
            a.download = 'plan_squadra.gpx';
            a.click();
        };
    });
    </script>
    """
    m.get_root().html.add_child(folium.Element(routing_js))

    folium.LayerControl(collapsed=False).add_to(m)
    Fullscreen().add_to(m)
    LocateControl().add_to(m)

    # Stats mensuelles
    now = datetime.now()
    this_m_str = now.strftime("%Y-%m")
    last_m_str = (now.replace(day=1) - timedelta(days=1)).strftime("%Y-%m")
    km_this, km_last = 0, 0
    for t in db["tour_details"].values():
        d = t.get('distance', 0) / 1000
        dt = t.get('date', '')
        if dt.startswith(this_m_str): km_this += d
        elif dt.startswith(last_m_str): km_last += d

    percent_fr = (len(db.get("tiles", [])) / TOTAL_TILES_FRANCE) * 100
    
    # Dashboard HTML
    sorted_tours = sorted(db["tour_details"].values(), key=lambda x: x.get('date') or "", reverse=True)
    list_html = "".join([f"<tr><td>{t.get('date')[:10]}</td><td>{t['name'][:15]}</td><td><b>{round(t['distance']/1000,1)}k</b></td><td>{t.get('elevation_up',0)}m</td></tr>" for t in sorted_tours[:8]])

    html_dash = f"""
    <style>
        #dash {{ position: fixed; top: 10px; right: 10px; width: 300px; z-index: 9999; background: rgba(255,255,255,0.95); 
                border-radius: 10px; box-shadow: 0 0 15px rgba(0,0,0,0.2); font-family: sans-serif; transition: 0.3s; overflow: hidden; }}
        #dash.collapsed {{ width: 45px; height: 45px; cursor: pointer; }}
        .h {{ background: #2c3e50; color: white; padding: 12px; cursor: pointer; display: flex; justify-content: space-between; font-weight: bold; }}
        .c {{ padding: 15px; font-size: 12px; max-height: 80vh; overflow-y: auto; color: black; }}
        .st {{ display: flex; justify-content: space-between; margin-bottom: 10px; background: #f8f9fa; padding: 8px; border-radius: 5px; }}
        .val {{ font-size: 14px; font-weight: bold; color: #e67e22; }}
        table {{ width: 100%; border-collapse: collapse; margin-top: 10px; font-size: 11px; color: black; }}
        td {{ padding: 5px 0; border-bottom: 1px solid #eee; }}
        .trend {{ color: {'green' if km_this >= km_last else 'red'}; font-weight: bold; }}
    </style>
    <div id="dash">
        <div class="h" onclick="document.getElementById('dash').classList.toggle('collapsed')"><span>🚴‍♂️ Squadra Dashboard</span><span>☰</span></div>
        <div class="c">
            <div class="st"><div>Mois en cours<br><span class="val">{int(km_this)} km</span></div><div style="text-align:right">Mois dernier<br><span>{int(km_last)} km</span></div></div>
            <div style="margin-bottom:10px">Tendance: <span class="trend">{'▲' if km_this >= km_last else '▼'} {int(abs(km_this-km_last))} km</span></div>
            <hr>
            <div>Exploration France: <b>{percent_fr:.4f}%</b></div>
            <div style="width:100%; background:#eee; height:8px; border-radius:4px; margin: 5px 0 15px 0;"><div style="width:{min(percent_fr*500, 100)}%; background:orange; height:100%; border-radius:4px;"></div></div>
            <b>Dernières sorties :</b>
            <table>{list_html}</table>
        </div>
    </div>
    """
    m.get_root().html.add_child(folium.Element(html_dash))
    m.save("index.html")
    logger.info("✅ Carte générée avec succès.")

if __name__ == "__main__":
    if USER_ID: update_database(USER_ID)
