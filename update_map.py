import os
import requests
import gpxpy
import folium
import logging
import time
from folium.plugins import HeatMap
from datetime import datetime

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger()

# --- CONFIGURATION ---
# Remplacez par votre chiffre trouvé dans l'URL de votre profil Komoot
USER_ID = "1366042741035" 
# --- INFOS GITHUB ---
REPO_OWNER = "CyrilHussenet"
REPO_NAME = "squamoot"

def run_sync():
    session = requests.Session()
    session.headers.update({'User-Agent': 'Mozilla/5.0'})
    all_points = []
    total_dist, total_elev, count = 0.0, 0.0, 0
    
    try:
        logger.info(f"Récupération des tours publics pour l'ID : {USER_ID}...")
        page = 0
        while True:
            # Cette URL ne nécessite pas de connexion si les tours sont publics
            tours_url = f"https://www.komoot.com/api/v1/users/{USER_ID}/tours/?type=tour_recorded&limit=100&page={page}"
            resp = session.get(tours_url)
            
            if resp.status_code != 200:
                logger.error(f"Erreur API {resp.status_code}. Vérifiez votre USER_ID.")
                break
            
            tours = resp.json().get('_embedded', {}).get('tours', [])
            if not tours: break

            for tour in tours:
                # Récupération du GPX
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
            
            logger.info(f"Page {page} traitée...")
            page += 1

        if all_points:
            avg_lat = sum(p[0] for p in all_points) / len(all_points)
            avg_lon = sum(p[1] for p in all_points) / len(all_points)
            
            m = folium.Map(location=[avg_lat, avg_lon], zoom_start=11, tiles='CartoDB dark_matter')
            HeatMap(all_points, radius=4, blur=2, min_opacity=0.4, gradient={0.4:'blue', 0.7:'cyan', 1:'white'}).add_to(m)

            # Tableau de bord
            header_html = f'''
            <div style="position:fixed; top:10px; left:50px; z-index:1000; background:rgba(26,26,26,0.9); color:white; padding:15px; border-radius:10px; border:1px solid cyan; font-family:sans-serif;">
                <h2 style="margin:0; font-size:16px; color:cyan;">Ma Squadra</h2>
                <p>{count} sorties | {round(total_dist/1000)} km | {int(total_elev)}m D+</p>
            </div>
            <button onclick="triggerUpdate()" style="position:fixed; top:10px; right:10px; z-index:1000; background:#1a1a1a; color:cyan; border:1px solid cyan; padding:10px; cursor:pointer; border-radius:5px;">🔄 Actualiser</button>
            <script>
            function triggerUpdate() {{
                const token = prompt("Token GitHub :");
                if(!token) return;
                fetch('https://api.github.com/repos/{REPO_OWNER}/{REPO_NAME}/actions/workflows/main.yml/dispatches', {{
                    method:'POST', headers:{{'Authorization':'Bearer '+token}}, body:JSON.stringify({{ref:'main'}})
                }}).then(r=>alert(r.ok?"Lancement !":"Erreur"));
            }}
            </script>
            '''
            m.get_root().html.add_child(folium.Element(header_html))
            m.save("index.html")
            logger.info("index.html créé !")
        else:
            logger.warning("Aucun point trouvé. Vos sorties sont-elles bien en mode PUBLIC ?")

    except Exception as e:
        logger.error(f"Erreur : {e}")

if __name__ == "__main__":
    run_sync()
