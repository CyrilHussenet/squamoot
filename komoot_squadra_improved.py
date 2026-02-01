import os
import requests
import gpxpy
import folium
import logging
import json
import math
import time
from folium.plugins import Fullscreen
from datetime import datetime
from collections import defaultdict

# Configuration du logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger()

# Configuration (Secrets GitHub)
USER_ID = os.getenv("KOMOOT_USER_ID")
SESSION_COOKIE = os.getenv("KOMOOT_SESSION_COOKIE")
REPO_OWNER = os.getenv("REPO_OWNER", "VOTRE_NOM_UTILISATEUR_GITHUB")
REPO_NAME = os.getenv("REPO_NAME", "VOTRE_NOM_DE_DEPOT")

# Configuration de l'affichage
DATA_FILE = "all_points.json"
SIMPLIFY_FACTOR = int(os.getenv("SIMPLIFY_FACTOR", "3"))  # 1 point sur N
TILE_ZOOM = int(os.getenv("TILE_ZOOM", "14"))  # Niveau de zoom des tiles
TRACE_OPACITY = float(os.getenv("TRACE_OPACITY", "0.6"))
TILE_OPACITY = float(os.getenv("TILE_OPACITY", "0.15"))

# Couleurs personnalisables
TILE_COLOR = os.getenv("TILE_COLOR", "#7ED321")  # Vert Komoot
TRACE_COLOR = os.getenv("TRACE_COLOR", "#D0021B")  # Rouge


def get_tile_coords(lat, lon, zoom=TILE_ZOOM):
    """Convertit lat/lon en coordonnées de tile OSM"""
    lat_rad = math.radians(lat)
    n = 2.0 ** zoom
    xtile = int((lon + 180.0) / 360.0 * n)
    ytile = int((1.0 - math.log(math.tan(lat_rad) + (1 / math.cos(lat_rad))) / math.pi) / 2.0 * n)
    return xtile, ytile


def get_tile_rect(xtile, ytile, zoom=TILE_ZOOM):
    """Retourne les bounds [SW, NE] d'une tile"""
    n = 2.0 ** zoom
    
    def tile_to_latlon(x, y):
        lon = x / n * 360.0 - 180.0
        lat = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * y / n))))
        return [lat, lon]
    
    return [tile_to_latlon(xtile, ytile), tile_to_latlon(xtile + 1, ytile + 1)]


def calculate_max_cluster(tiles_set):
    """Calcule la taille du plus grand cluster contigu de tiles (BFS)"""
    if not tiles_set:
        return 0
    
    visited = set()
    max_cluster = 0
    tiles_list = list(tiles_set)
    
    for tile in tiles_list:
        if tile not in visited:
            cluster_size = 0
            queue = [tile]
            visited.add(tile)
            
            while queue:
                curr = queue.pop(0)
                cluster_size += 1
                
                # Vérifier les 4 voisins (haut, bas, gauche, droite)
                for dx, dy in [(0, 1), (0, -1), (1, 0), (-1, 0)]:
                    neighbor = (curr[0] + dx, curr[1] + dy)
                    if neighbor in tiles_set and neighbor not in visited:
                        visited.add(neighbor)
                        queue.append(neighbor)
            
            max_cluster = max(max_cluster, cluster_size)
    
    return max_cluster


def load_existing_data():
    """Charge les données existantes depuis le fichier JSON"""
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, 'r') as f:
                data = json.load(f)
                logger.info(f"✓ Données chargées: {len(data.get('traces', []))} traces, {len(data.get('tour_ids', []))} tours")
                return data
        except json.JSONDecodeError as e:
            logger.warning(f"Erreur lecture JSON, réinitialisation: {e}")
        except Exception as e:
            logger.warning(f"Erreur chargement données: {e}")
    
    # Structure de données initiale
    return {
        "traces": [],           # Liste de traces (lignes de coordonnées)
        "tour_ids": [],         # IDs des tours déjà synchronisés
        "last_tours": [],       # 5 derniers tours pour affichage
        "stats": {
            "dist": 0,          # Distance totale (non utilisé actuellement)
            "count": 0          # Nombre de tours
        }
    }


def save_data(storage):
    """Sauvegarde les données dans le fichier JSON"""
    try:
        with open(DATA_FILE, 'w') as f:
            json.dump(storage, f, indent=2)
        logger.info(f"✓ Données sauvegardées: {len(storage['traces'])} traces")
    except Exception as e:
        logger.error(f"Erreur sauvegarde données: {e}")


def get_session_with_retry(max_retries=3):
    """Crée une session 'Stealth' qui imite parfaitement Chrome pour passer le WAF Komoot"""
    session = requests.Session()
    
    # Nettoyage et vérification du cookie
    cookie_val = SESSION_COOKIE.strip() if SESSION_COOKIE else ""
    if "komoot_session=" in cookie_val:
        cookie_val = cookie_val.split("komoot_session=")[1].split(";")[0]
    
    if not cookie_val:
        logger.error("❌ Le cookie SESSION_COOKIE est vide ou mal configuré.")
        return None

    # HEADERS AVANCÉS (Mimétisme Chrome complet)
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36',
        'Accept': 'application/json, text/plain, */*',
        'Accept-Language': 'fr-FR,fr;q=0.9,en-US;q=0.8,en;q=0.7',
        'Referer': f'https://www.komoot.com/user/{USER_ID}/tours',
        'Origin': 'https://www.komoot.com',
        'DNT': '1',
        'Connection': 'keep-alive',
        'Sec-Fetch-Dest': 'empty',
        'Sec-Fetch-Mode': 'cors',
        'Sec-Fetch-Site': 'same-origin',
        'sec-ch-ua': '"Not A(Brand";v="99", "Google Chrome";v="121", "Chromium";v="121"',
        'sec-ch-ua-mobile': '?0',
        'sec-ch-ua-platform': '"Windows"',
        'Cookie': f'komoot_session={cookie_val}'
    }
    
    session.headers.update(headers)
    
    for attempt in range(max_retries):
        try:
            logger.info(f"⏳ Tentative de connexion {attempt + 1}/{max_retries}...")
            
            # On teste sur l'API user profile d'abord (souvent moins protégée que /tours)
            test_url = f"https://www.komoot.com/api/v007/users/{USER_ID}/tours/?limit=1"
            
            response = session.get(test_url, timeout=15)
            
            if response.status_code == 200:
                logger.info(f"✓ Session validée et connectée !")
                return session
            elif response.status_code == 403:
                logger.warning(f"⚠️ 403 Forbidden - Komoot bloque encore. Vérifiez que le cookie n'est pas expiré.")
            elif response.status_code == 401:
                logger.error("❌ 401 Unauthorized - Le cookie est invalide ou expiré. Il faut en récupérer un nouveau.")
                return None
            else:
                logger.warning(f"Statut inattendu: {response.status_code}")
                
        except requests.exceptions.RequestException as e:
            logger.warning(f"Erreur réseau: {e}")
        
        time.sleep(3 + attempt)  # Pause plus longue entre les essais
    
    logger.error("❌ Échec de la connexion après plusieurs tentatives.")
    return None

def fetch_tours_page(session, sort_direction='desc', limit=50):
    """Récupère une page de tours depuis l'API Komoot"""
    url = (f"https://www.komoot.com/api/v007/users/{USER_ID}/tours/"
           f"?type=tour_recorded&sort_field=date&sort_direction={sort_direction}&limit={limit}")
    
    try:
        resp = session.get(url, timeout=10)
        resp.raise_for_status()
        return resp.json().get('_embedded', {}).get('tours', [])
    except requests.exceptions.RequestException as e:
        logger.error(f"Erreur récupération tours: {e}")
        return []


def fetch_tour_gpx(session, tour_id):
    """Récupère et parse le fichier GPX d'un tour"""
    try:
        res_gpx = session.get(f"https://www.komoot.com/api/v1/tours/{tour_id}.gpx", timeout=10)
        
        if res_gpx.status_code != 200:
            logger.warning(f"Impossible de récupérer le GPX du tour {tour_id}")
            return None
        
        gpx = gpxpy.parse(res_gpx.text)
        return gpx
        
    except gpxpy.gpx.GPXException as e:
        logger.warning(f"Erreur parsing GPX pour tour {tour_id}: {e}")
        return None
    except Exception as e:
        logger.error(f"Erreur inattendue pour tour {tour_id}: {e}")
        return None


def simplify_trace(points, factor=SIMPLIFY_FACTOR):
    """Simplifie une trace en ne gardant qu'un point sur N"""
    if factor <= 1:
        return points
    return points[::factor]


def extract_traces_from_gpx(gpx):
    """Extrait les traces (lignes) d'un fichier GPX"""
    traces = []
    
    for track in gpx.tracks:
        for seg in track.segments:
            # Simplification et arrondi des coordonnées
            points = [[round(p.latitude, 5), round(p.longitude, 5)] for p in seg.points]
            simplified = simplify_trace(points)
            
            if len(simplified) > 1:
                traces.append(simplified)
    
    return traces


def calculate_month_stats(tours_data):
    """Calcule les statistiques du mois en cours"""
    current_month = datetime.now().strftime("%Y-%m")
    month_dist = 0
    month_time_sec = 0
    
    for tour in tours_data:
        if tour['date'].startswith(current_month):
            month_dist += tour.get('distance', 0)
            month_time_sec += tour.get('duration', 0)
    
    return month_dist, month_time_sec


def format_last_tours(tours_data, limit=5):
    """Formate les derniers tours pour l'affichage"""
    return [{
        "name": t["name"],
        "date": t["date"][:10],
        "dist": round(t.get("distance", 0) / 1000, 1)
    } for t in tours_data[:limit]]


def create_map_with_tiles_and_traces(storage):
    """Crée la carte Folium avec tiles et traces"""
    
    # Calcul du centre de la carte
    all_coords = []
    for trace in storage["traces"]:
        all_coords.extend(trace)
    
    if not all_coords:
        logger.warning("Aucune coordonnée disponible, utilisation du centre par défaut")
        center = [46.5, 2.2]  # Centre France
        zoom = 6
    else:
        avg_lat = sum(c[0] for c in all_coords) / len(all_coords)
        avg_lon = sum(c[1] for c in all_coords) / len(all_coords)
        center = [avg_lat, avg_lon]
        zoom = 7
    
    # Création de la carte avec style OSM Allemagne (style Komoot)
    m = folium.Map(
        location=center,
        zoom_start=zoom,
        tiles='https://{s}.tile.openstreetmap.de/tiles/osmde/{z}/{x}/{y}.png',
        attr='&copy; OpenStreetMap contributors'
    )
    
    # Plugin plein écran
    Fullscreen(position='topleft').add_to(m)
    
    # 1. CALCUL ET AFFICHAGE DES TILES
    visited_tiles = set()
    for trace in storage["traces"]:
        for point in trace:
            visited_tiles.add(get_tile_coords(point[0], point[1]))
    
    logger.info(f"📍 {len(visited_tiles)} tiles uniques visitées")
    
    for tile in visited_tiles:
        folium.Rectangle(
            bounds=get_tile_rect(tile[0], tile[1]),
            color=TILE_COLOR,
            fill=True,
            fill_color=TILE_COLOR,
            fill_opacity=TILE_OPACITY,
            weight=0.5,
            opacity=0.8
        ).add_to(m)
    
    # 2. AFFICHAGE DES TRACES (LIGNES)
    logger.info(f"🗺️ Ajout de {len(storage['traces'])} traces")
    
    for trace in storage["traces"]:
        folium.PolyLine(
            trace,
            color=TRACE_COLOR,
            weight=2,
            opacity=TRACE_OPACITY
        ).add_to(m)
    
    # 3. CALCUL DU PLUS GRAND CLUSTER
    max_cluster = calculate_max_cluster(visited_tiles)
    logger.info(f"🔗 Plus grand cluster: {max_cluster} tiles")
    
    return m, visited_tiles, max_cluster


def create_sidebar_html(storage, visited_tiles_count, max_cluster, month_dist, month_time_sec):
    """Génère le HTML de la sidebar avec statistiques"""
    
    # Formatage du temps
    hours = int(month_time_sec // 3600)
    minutes = int((month_time_sec % 3600) // 60)
    
    # Liste des derniers parcours
    tours_html = ""
    for tour in storage.get("last_tours", []):
        tours_html += f'''
        <div style='border-bottom:1px solid #eee; padding:5px 0;'>
            <b style="font-size:11px;">{tour['name']}</b><br>
            <small style="color:#999;">{tour['date']} - {tour['dist']} km</small>
        </div>
        '''
    
    current_month_name = datetime.now().strftime('%B %Y').upper()
    
    sidebar_html = f'''
    <div id="sidebar" style="position:fixed; top:10px; right:10px; width:240px; z-index:1000; 
         background:white; color:#333; padding:18px; border-radius:12px; 
         font-family:'Segoe UI', sans-serif; border:1px solid #ddd; 
         box-shadow: 0 4px 15px rgba(0,0,0,0.15); font-size:12px;">
        
        <!-- Header -->
        <div style="text-align:center; margin-bottom:12px;">
            <b style="color:{TILE_COLOR}; font-size:18px; letter-spacing:1px;">SQUADRA MAP</b>
        </div>
        
        <!-- Bilan du mois -->
        <div style="background:#f0f7e7; padding:12px; border-radius:10px; margin-bottom:12px; 
                    border:1px solid {TILE_COLOR}; text-align:center;">
            <b style="font-size:10px; color:#5a9616; text-transform:uppercase;">📅 {current_month_name}</b><br>
            <b style="font-size:20px; color:{TILE_COLOR};">{round(month_dist/1000, 1)} km</b><br>
            <small style="color:#666;">{hours}h {minutes}min en selle</small>
        </div>
        
        <!-- Statistiques principales -->
        <div style="display:flex; justify-content:space-around; margin-bottom:12px; 
                    text-align:center; background:#f9f9f9; padding:10px; border-radius:8px;">
            <div>
                <b style="font-size:18px; color:{TILE_COLOR};">{visited_tiles_count}</b><br>
                <small style="color:#999; font-size:10px;">TILES</small>
            </div>
            <div>
                <b style="font-size:18px; color:#FF6B35;">{max_cluster}</b><br>
                <small style="color:#999; font-size:10px;">CLUSTER</small>
            </div>
            <div>
                <b style="font-size:18px; color:#4A90E2;">{storage["stats"]["count"]}</b><br>
                <small style="color:#999; font-size:10px;">TOURS</small>
            </div>
        </div>
        
        <!-- Derniers parcours -->
        <div style="border-top:1px solid #eee; padding-top:10px;">
            <b style="color:#999; font-size:10px; text-transform:uppercase;">📍 Derniers Parcours</b>
            <div style="max-height:180px; overflow-y:auto; margin-top:8px;">
                {tours_html}
            </div>
        </div>
        
        <!-- Bouton refresh -->
        <button onclick="triggerUpdate()" 
                style="width:100%; margin-top:12px; padding:10px; background:{TILE_COLOR}; 
                       color:white; border:none; border-radius:8px; cursor:pointer; 
                       font-weight:bold; font-size:12px; transition:all 0.3s;"
                onmouseover="this.style.opacity='0.8'"
                onmouseout="this.style.opacity='1'">
            🔄 ACTUALISER
        </button>
        
        <!-- Info mise à jour -->
        <div style="margin-top:10px; padding-top:8px; border-top:1px solid #eee; 
                    font-size:9px; color:#999; text-align:center;">
            Mise à jour: {datetime.now().strftime('%d/%m/%Y %H:%M')}
        </div>
    </div>
    
    <script>
    function triggerUpdate() {{
        const token = prompt("Entrez votre GitHub Personal Access Token :\\n(Permissions: repo, workflow)");
        if (!token) return;
        
        const repoOwner = "{REPO_OWNER}";
        const repoName = "{REPO_NAME}";
        
        if (repoOwner === "VOTRE_NOM_UTILISATEUR_GITHUB" || repoName === "VOTRE_NOM_DE_DEPOT") {{
            alert("⚠️ Erreur de configuration\\nVeuillez configurer GITHUB_REPO_OWNER et GITHUB_REPO_NAME dans les secrets.");
            return;
        }}
        
        fetch(`https://api.github.com/repos/${{repoOwner}}/${{repoName}}/actions/workflows/main.yml/dispatches`, {{
            method: 'POST',
            headers: {{ 
                'Authorization': 'Bearer ' + token, 
                'Accept': 'application/vnd.github.v3+json',
                'Content-Type': 'application/json'
            }},
            body: JSON.stringify({{ ref: 'main' }})
        }})
        .then(res => {{
            if (res.ok) {{
                alert("✅ Synchronisation lancée !\\n\\nRevenez dans 2-3 minutes pour voir les nouvelles données.");
                window.location.reload();
            }} else {{
                return res.json().then(data => {{
                    alert("❌ Erreur: " + (data.message || "Token invalide ou permissions insuffisantes"));
                }});
            }}
        }})
        .catch(err => {{
            alert("❌ Erreur réseau: " + err.message);
        }});
    }}
    </script>
    '''
    
    return sidebar_html


def run_sync():
    """Fonction principale de synchronisation"""
    
    # Validation des credentials
    if not USER_ID or not SESSION_COOKIE:
        logger.error("❌ Configuration manquante: KOMOOT_USER_ID et KOMOOT_SESSION_COOKIE requis")
        return
    
    logger.info("=" * 60)
    logger.info("🚀 Démarrage de la synchronisation Komoot")
    logger.info("=" * 60)
    
    # Chargement des données existantes
    storage = load_existing_data()
    
    # Création de la session
    session = get_session_with_retry()
    if not session:
        logger.error("❌ Impossible de créer une session valide")
        return
    
    try:
        # Récupération des tours
        logger.info("📥 Récupération des tours depuis Komoot...")
        tours_data = fetch_tours_page(session)
        
        if not tours_data:
            logger.warning("⚠️ Aucun tour récupéré")
            return
        
        logger.info(f"✓ {len(tours_data)} tours récupérés")
        
        # Calcul des stats du mois
        month_dist, month_time_sec = calculate_month_stats(tours_data)
        logger.info(f"📊 Mois en cours: {round(month_dist/1000, 1)} km, "
                   f"{int(month_time_sec//3600)}h {int((month_time_sec%3600)//60)}min")
        
        # Mise à jour de la liste des derniers tours
        storage["last_tours"] = format_last_tours(tours_data)
        
        # Synchronisation des nouveaux tours
        new_tours_count = 0
        for tour in tours_data:
            tour_id = str(tour['id'])
            
            if tour_id not in storage["tour_ids"]:
                logger.info(f"🔄 Synchronisation du tour {tour_id}: {tour.get('name', 'Sans nom')}")
                
                gpx = fetch_tour_gpx(session, tour_id)
                if gpx:
                    traces = extract_traces_from_gpx(gpx)
                    storage["traces"].extend(traces)
                    storage["tour_ids"].append(tour_id)
                    storage["stats"]["count"] += 1
                    new_tours_count += 1
                    logger.info(f"  ✓ {len(traces)} trace(s) extraite(s)")
                
                time.sleep(0.1)  # Rate limiting
        
        if new_tours_count > 0:
            logger.info(f"✅ {new_tours_count} nouveau(x) tour(s) synchronisé(s)")
        else:
            logger.info("✓ Aucun nouveau tour à synchroniser")
        
        # Sauvegarde des données
        save_data(storage)
        
        # Génération de la carte
        if storage.get("traces"):
            logger.info("🗺️ Génération de la carte...")
            
            m, visited_tiles, max_cluster = create_map_with_tiles_and_traces(storage)
            
            # Ajout de la sidebar
            sidebar_html = create_sidebar_html(
                storage, 
                len(visited_tiles), 
                max_cluster, 
                month_dist, 
                month_time_sec
            )
            m.get_root().html.add_child(folium.Element(sidebar_html))
            
            # Sauvegarde de la carte
            m.save("index.html")
            logger.info("✅ Carte générée: index.html")
            
            # Résumé final
            logger.info("=" * 60)
            logger.info("📊 RÉSUMÉ")
            logger.info("=" * 60)
            logger.info(f"  Tours totaux: {storage['stats']['count']}")
            logger.info(f"  Traces: {len(storage['traces'])}")
            logger.info(f"  Tiles visitées: {len(visited_tiles)}")
            logger.info(f"  Plus grand cluster: {max_cluster}")
            logger.info(f"  Mois en cours: {round(month_dist/1000, 1)} km")
            logger.info("=" * 60)
        else:
            logger.warning("⚠️ Aucune trace disponible pour générer la carte")
    
    except Exception as e:
        logger.error(f"❌ Erreur critique: {e}", exc_info=True)
        raise


if __name__ == "__main__":
    run_sync()
