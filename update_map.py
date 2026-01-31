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

EMAIL = os.getenv("KOMOOT_EMAIL")
PASSWORD = os.getenv("KOMOOT_PASSWORD")
REPO_OWNER = "CyrilHussenet" # <--- VÉRIFIEZ BIEN CECI
REPO_NAME = "squamoot"   # <--- VÉRIFIEZ BIEN CECI

def run_sync():
    session = requests.Session()
    session.headers.update({'User-Agent': 'Mozilla/5.0'})
    all_points = []
    total_dist, total_elev, count = 0.0, 0.0, 0
    
    try:
        logger.info("Tentative de connexion à Komoot via /login...")
        # L'URL CRUCIALE EST ICI : /login
        response = session.post("https://www.komoot.com/api/v1/login", 
                                data={'email': EMAIL, 'password': PASSWORD})
        
        if response.status_code != 200:
            logger.error(f"Echec (Code {response.status_code}). Vérifiez EMAIL/PASSWORD dans les Secrets.")
            return

        user_id = response.json().get('username')
        logger.info(f"Connecté : {user_id}")

        page = 0
        while True:
            tours_url = f"https://www.komoot.com/api/v1/users/{user_id}/tours/?type=tour_recorded&limit=100&page={page}"
            t_resp = session.get(tours_url)
            if t_resp.status_code != 200: break
            tours = t_resp.json().get('_embedded', {}).get('tours', [])
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
            page += 1

        if all_points:
            avg_lat = sum(p[0] for p in all_points) / len(all_points)
            avg_lon = sum(p[1] for p in all_points) / len(all_points)
            m = folium.Map(location=[avg_lat, avg_lon], zoom_start=11, tiles='CartoDB dark_matter')
            HeatMap(all_points, radius=4, blur=2, min_opacity=0.4, gradient={0.4:'blue', 0.7:'cyan', 1:'white'}).add_to(m)
            
            # Injection du HTML
            m.get_root().html.add_child(folium.Element(f'''
                <div style="position:fixed; top:10px; left:50px; z-index:1000; background:rgba(26,26,26,0.9); color:white; padding:15px; border-radius:10px; border:1px solid cyan; font-family:sans-serif;">
                    <h2 style="margin:0; font-size:16px; color:cyan;">Squadra Komoot</h2>
                    <p>{count} activités | {round(total_dist/1000)} km | {int(total_elev)}m D+</p>
                </div>
            '''))
            m.save("index.html")
            logger.info("index.html généré avec succès.")
    except Exception as e:
        logger.error(f"Erreur : {e}")

if __name__ == "__main__":
    run_sync()
