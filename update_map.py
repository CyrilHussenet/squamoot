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
USER_ID = "1366042741035" # Votre ID vérifié
REPO_OWNER = "CyrilHussenet"
REPO_NAME = "squamoot"

def run_sync():
    session = requests.Session()
    # On simule un navigateur récent pour éviter les blocages de sécurité
    session.headers.update({
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept': 'application/json, text/plain, */*',
        'Accept-Language': 'fr-FR,fr;q=0.9,en-US;q=0.8,en;q=0.7',
    })
    
    all_points = []
    total_dist, total_elev, count = 0.0, 0.0, 0
    
    try:
        logger.info(f"Démarrage de la synchronisation pour l'ID : {USER_ID}")
        
        page = 0
        while True:
            # On utilise l'URL de l'API avec le paramètre de statut 'public' explicite
            tours_url = f"https://www.komoot.com/api/v1/users/{USER_ID}/tours/?type=tour_recorded&status=public&limit=100&page={page}"
            resp = session.get(tours_url)
            
            if resp.status_code != 200:
                logger.error(f"Erreur API {resp.status_code} à la page {page}. Réponse : {resp.text[:100]}")
                break
                
            data = resp.json()
            tours = data.get('_embedded', {}).get('tours', [])
            
            if not tours:
                logger.info("Plus aucune sortie à récupérer.")
                break

            for tour in tours:
                # Récupération du GPX
                gpx_url = f"https://www.komoot.com/api/v1/tours/{tour['id']}.gpx"
                gpx_res = session.get(gpx_url)
                
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
                    except Exception as e:
                        continue
                
                # Délai très court pour ne pas être banni
                time.sleep(0.05)
            
            logger.info(f"Page {page} traitée ({count} sorties cumulées).")
            page += 1

        if all_points:
            # Calcul du centre de la carte
            avg_lat = sum(p[0] for p in all_points) / len(all_points)
            avg_lon = sum(p[1] for p in all_points) / len(all_points)
            
            m = folium.Map(location=[avg_lat, avg_lon], zoom_start=11, tiles='CartoDB dark_matter')
            
            # Heatmap style Squadra
            HeatMap(all_points, radius=4, blur=2, min_opacity=0.4, gradient={0.4:'blue', 0.7:'cyan', 1:'white'}).add_to(m)

            # Interface Dashboard
            header_html = f'''
            <div style="position:fixed; top:10px; left:50px; z-index:1000; background:rgba(20,20,20,0.9); color:white; padding:15px; border-radius:10px; border:1px solid #00f2ff; font-family:sans-serif; box-shadow: 0 0 15px rgba(0,242,255,0.2);">
                <h2 style="margin:0 0 5px 0; font-size:18px; color:#00f2ff; letter-spacing:1px;">SQUADRA MAP</h2>
                <p style="margin:0; font-size:14px; opacity:0.9;">
                    <b>{count}</b> Sorties | <b>{round(total_dist/1000)}</b> km | <b>{int(total_elev)}</b>m D+
                </p>
                <div style="font-size:9px; color:gray; margin-top:5px;">ID: {USER_ID} | MAJ: {datetime.now().strftime('%H:%M')}</div>
            </div>
            <button onclick="triggerUpdate()" style="position:fixed; top:10px; right:10px; z-index:1000; background:#1a1a1a; color:#00f2ff; border:1px solid #00f2ff; padding:12px 20px; cursor:pointer; border-radius:5px; font-weight:bold; transition: 0.3s;" onmouseover="this.style.background='#00f2ff'; this.style.color='#000';" onmouseout="this.style.background='#1a1a1a'; this.style.color='#00f2ff';">
                🔄 ACTUALISER
            </button>
            <script>
            function triggerUpdate() {{
                const token = prompt("Token GitHub :");
                if(!token) return;
                fetch('https://api.github.com/repos/{REPO_OWNER}/{REPO_NAME}/actions/workflows/main.yml/dispatches', {{
                    method:'POST', headers:{{'Authorization':'Bearer '+token, 'Accept':'application/vnd.github.v3+json'}}, body:JSON.stringify({{ref:'main'}})
                }}).then(r=>alert(r.ok?"Action lancée ! Patientez 2 minutes.":"Erreur de Token"));
            }}
            </script>
            '''
            m.get_root().html.add_child(folium.Element(header_html))
            m.save("index.html")
            logger.info("Succès : index.html généré avec les statistiques.")
        else:
            logger.warning("Connexion réussie mais aucune donnée trouvée. Vérifiez vos paramètres Komoot.")

    except Exception as e:
        logger.error(f"Erreur critique : {e}")

if __name__ == "__main__":
    run_sync()
