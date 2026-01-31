import os
import requests
import gpxpy
import folium
import logging
import time
from folium.plugins import HeatMap
from datetime import datetime

# Configuration du log
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger()

# Configuration (Secrets GitHub)
EMAIL = os.getenv("KOMOOT_EMAIL")
PASSWORD = os.getenv("KOMOOT_PASSWORD")
REPO_OWNER = "VOTRE_NOM_UTILISATEUR_GITHUB" # À REMPLACER
REPO_NAME = "VOTRE_NOM_DE_DEPOT"           # À REMPLACER

def run_sync():
    if not EMAIL or not PASSWORD:
        logger.error("Identifiants manquants.")
        return

    session = requests.Session()
    all_points = []
    total_dist = 0.0
    total_elev = 0.0
    count = 0
    
    try:
        logger.info("Connexion à Komoot...")
        login_url = "https://www.komoot.com/api/v1/signin"
        response = session.post(login_url, data={'email': EMAIL, 'password': PASSWORD})
        response.raise_for_status()
        user_id = response.json()['username']

        page = 0
        while True:
            tours_url = f"https://www.komoot.com/api/v1/users/{user_id}/tours/?type=tour_recorded&limit=100&page={page}"
            tours = session.get(tours_url).json().get('_embedded', {}).get('tours', [])
            if not tours: break

            for tour in tours:
                gpx_res = session.get(f"https://www.komoot.com/api/v1/tours/{tour['id']}.gpx")
                if gpx_res.status_code == 200:
                    try:
                        gpx = gpxpy.parse(gpx_res.text)
                        total_dist += gpx.length_2d()
                        total_elev += gpx.get_uphill_downhill().uphill
                        count += 1
                        for track in gpx.tracks:
                            for seg in track.segments:
                                for p in seg.points:
                                    all_points.append([p.latitude, p.longitude])
                    except: continue
                time.sleep(0.1)
            logger.info(f"Page {page} terminée.")
            page += 1

        if all_points:
            avg_lat = sum(p[0] for p in all_points) / len(all_points)
            avg_lon = sum(p[1] for p in all_points) / len(all_points)
            
            m = folium.Map(location=[avg_lat, avg_lon], zoom_start=11, tiles='CartoDB dark_matter')
            HeatMap(all_points, radius=4, blur=2, min_opacity=0.4, gradient={0.4: 'blue', 0.7: 'cyan', 1: 'white'}).add_to(m)

            # HTML du Tableau de bord et du Bouton
            header_html = f"""
            <div style="position: fixed; top: 10px; left: 50px; z-index: 1000; background: rgba(26,26,26,0.8); 
                        color: white; padding: 15px; border-radius: 10px; border: 1px solid #00f2ff; font-family: sans-serif;">
                <h2 style="margin: 0 0 10px 0; font-size: 16px; color: #00f2ff;">Ma Squadra Komoot</h2>
                <div style="display: flex; gap: 20px;">
                    <div><b style="font-size: 20px;">{count}</b><br><small>SORTIES</small></div>
                    <div><b style="font-size: 20px;">{round(total_dist/1000, 1)}</b><br><small>KM</small></div>
                    <div><b style="font-size: 20px;">{int(total_elev)}</b><br><small>D+ (m)</small></div>
                </div>
            </div>

            <button onclick="triggerUpdate()" style="position: fixed; top: 10px; right: 10px; z-index: 1000; 
                background: #1a1a1a; color: #00f2ff; border: 1px solid #00f2ff; padding: 12px; 
                cursor: pointer; border-radius: 5px; font-weight: bold;">
                🔄 ACTUALISER
            </button>

            <script>
            function triggerUpdate() {{
                const token = prompt("Entrez votre GitHub Personal Access Token :");
                if (!token) return;
                fetch('https://api.github.com/repos/{REPO_OWNER}/{REPO_NAME}/actions/workflows/main.yml/dispatches', {{
                    method: 'POST',
                    headers: {{ 'Authorization': 'Bearer ' + token, 'Accept': 'application/vnd.github.v3+json' }},
                    body: JSON.stringify({{ ref: 'main' }})
                }}).then(res => {{
                    if (res.ok) alert("Synchronisation lancée ! Revenez dans 2 minutes.");
                    else alert("Erreur de token ou de permissions.");
                }});
            }}
            </script>
            <div style="position: fixed; bottom: 10px; left: 10px; z-index: 1000; color: #555; font-size: 10px;">
                Mise à jour : {datetime.now().strftime('%d/%m/%Y %H:%M')}
            </div>
            """
            m.get_root().html.add_child(folium.Element(header_html))
            m.save("index.html")
            logger.info("Carte et tableau de bord générés.")

    except Exception as e:
        logger.error(f"Erreur : {e}")

if __name__ == "__main__":
    run_sync()